#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROS_SETUP="${ROS_SETUP:-/opt/ros/humble/setup.bash}"
ROS_WORKSPACE_SETUP="${ROS_WORKSPACE_SETUP:-${HOME}/ros2/install/setup.bash}"
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-30}"
PLATFORM_CAMERA_RUNTIME_DIR="${PLATFORM_CAMERA_RUNTIME_DIR:-/tmp/platform_camera}"
PLATFORM_CAMERA_LOG_DIR="${PLATFORM_CAMERA_LOG_DIR:-${HOME}/ros2/log/platform_camera}"
PLATFORM_CAMERA_PID_FILE="${PLATFORM_CAMERA_PID_FILE:-${PLATFORM_CAMERA_RUNTIME_DIR}/platform_camera.pid}"
PLATFORM_CAMERA_STOP_SCRIPT="${PLATFORM_CAMERA_STOP_SCRIPT:-${SCRIPT_DIR}/stop_platform_camera.sh}"
PLATFORM_CAMERA_COLOR_TOPIC="${PLATFORM_CAMERA_COLOR_TOPIC:-/camera/color/image_raw}"
PLATFORM_CAMERA_DEPTH_TOPIC="${PLATFORM_CAMERA_DEPTH_TOPIC:-/camera/depth/image_raw}"
PLATFORM_CAMERA_LAUNCH_CMD="${PLATFORM_CAMERA_LAUNCH_CMD:-ros2 launch orbbec_camera orbbec_camera.launch.py camera_type:=astraproplus enable_ir:=false enable_color:=true enable_depth:=true color_width:=640 color_height:=480 color_fps:=30 depth_width:=640 depth_height:=480 depth_fps:=30}"
PLATFORM_CAMERA_HEALTH_WAIT_SEC="${PLATFORM_CAMERA_HEALTH_WAIT_SEC:-12}"
PLATFORM_CAMERA_REQUIRE_DEPTH="${PLATFORM_CAMERA_REQUIRE_DEPTH:-1}"
PLATFORM_CAMERA_REQUIRE_COLOR_FRAME="${PLATFORM_CAMERA_REQUIRE_COLOR_FRAME:-1}"
PLATFORM_CAMERA_FRAME_WAIT_SEC="${PLATFORM_CAMERA_FRAME_WAIT_SEC:-2}"
PLATFORM_CAMERA_RESTART_ON_BAD_FRAME="${PLATFORM_CAMERA_RESTART_ON_BAD_FRAME:-1}"

DRY_RUN=0
STATUS_MODE=0

usage() {
  cat <<'EOF'
Usage: scripts/start_platform_camera.sh [options]

Start the platform depth/RGB ROS camera publisher. This is the chassis/platform
Orbbec/Astra camera, not the arm reading camera.

Options:
  --status      Print publisher counts for RGB/depth topics.
  --dry-run     Print commands without executing them.
  -h, --help    Show this help.

Environment:
  PLATFORM_CAMERA_LAUNCH_CMD      Default: ros2 launch orbbec_camera orbbec_camera.launch.py camera_type:=astraproplus enable_ir:=false enable_color:=true enable_depth:=true color_width:=640 color_height:=480 color_fps:=30 depth_width:=640 depth_height:=480 depth_fps:=30
  PLATFORM_CAMERA_COLOR_TOPIC     Default: /camera/color/image_raw
  PLATFORM_CAMERA_DEPTH_TOPIC     Default: /camera/depth/image_raw
  PLATFORM_CAMERA_REQUIRE_DEPTH   Default: 1
  PLATFORM_CAMERA_REQUIRE_COLOR_FRAME Default: 1
  PLATFORM_CAMERA_FRAME_WAIT_SEC   Default: 2
  PLATFORM_CAMERA_RESTART_ON_BAD_FRAME Default: 1
EOF
}

log() {
  printf '[platform_camera] %s\n' "$*"
}

source_if_readable() {
  local file="$1"
  if [[ -r "${file}" ]]; then
    if [[ "${DRY_RUN}" == "1" ]]; then
      log "[dry-run] source ${file}"
    else
      set +e
      set +u
      # shellcheck source=/dev/null
      source "${file}"
      local rc=$?
      set -e
      set -u
      return "${rc}"
    fi
  else
    log "setup file not found, skipping: ${file}"
  fi
}

topic_publishers() {
    local topic="$1"
    local info count
    info="$(timeout 5 ros2 topic info -v "${topic}" 2>/dev/null || true)"
    count="$(printf '%s\n' "${info}" | awk -F': ' '/Publisher count/ {print $2; exit}' || true)"
    printf '%s\n' "${count:-0}"
}

topic_has_frame() {
    local topic="$1"
    timeout "${PLATFORM_CAMERA_FRAME_WAIT_SEC}" ros2 topic echo "${topic}" --once --field header >/dev/null 2>&1
}

show_status() {
  source_if_readable "${ROS_SETUP}" >/dev/null || true
  source_if_readable "${ROS_WORKSPACE_SETUP}" >/dev/null || true
  if ! command -v ros2 >/dev/null 2>&1; then
    log "ros2 command unavailable"
    return 1
  fi
  local color_publishers depth_publishers
  color_publishers="$(topic_publishers "${PLATFORM_CAMERA_COLOR_TOPIC}")"
  depth_publishers="$(topic_publishers "${PLATFORM_CAMERA_DEPTH_TOPIC}")"
  color_publishers="${color_publishers:-0}"
  depth_publishers="${depth_publishers:-0}"
  log "color ${PLATFORM_CAMERA_COLOR_TOPIC}: publishers=${color_publishers}"
  log "depth ${PLATFORM_CAMERA_DEPTH_TOPIC}: publishers=${depth_publishers}"
  if [[ -s "${PLATFORM_CAMERA_PID_FILE}" ]]; then
    local pid
    pid="$(cat "${PLATFORM_CAMERA_PID_FILE}" 2>/dev/null || true)"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      log "pid=${pid}"
    else
      log "stale pid file: ${PLATFORM_CAMERA_PID_FILE}"
    fi
  fi
}

already_has_publishers() {
  local color_publishers depth_publishers
  color_publishers="$(topic_publishers "${PLATFORM_CAMERA_COLOR_TOPIC}")"
  depth_publishers="$(topic_publishers "${PLATFORM_CAMERA_DEPTH_TOPIC}")"
  color_publishers="${color_publishers:-0}"
  depth_publishers="${depth_publishers:-0}"
  [[ "${color_publishers}" != "0" ]] || return 1
  [[ "${PLATFORM_CAMERA_REQUIRE_DEPTH}" != "1" || "${depth_publishers}" != "0" ]]
}

platform_camera_ready() {
  already_has_publishers || return 1
  if [[ "${PLATFORM_CAMERA_REQUIRE_COLOR_FRAME}" == "1" ]]; then
    topic_has_frame "${PLATFORM_CAMERA_COLOR_TOPIC}" || return 1
  fi
  return 0
}

log_frame_health() {
  local color_frame_ok="skipped"
  if [[ "${PLATFORM_CAMERA_REQUIRE_COLOR_FRAME}" == "1" ]]; then
    if topic_has_frame "${PLATFORM_CAMERA_COLOR_TOPIC}"; then
      color_frame_ok="1"
    else
      color_frame_ok="0"
    fi
  fi
  log "frame_health color_frame_ok=${color_frame_ok} require_color_frame=${PLATFORM_CAMERA_REQUIRE_COLOR_FRAME}"
}

stop_bad_platform_camera_process() {
  log "stopping stale platform camera publisher without required frames"
  if [[ -x "${PLATFORM_CAMERA_STOP_SCRIPT}" ]]; then
    "${PLATFORM_CAMERA_STOP_SCRIPT}" || true
  else
    log "platform camera stop script not executable: ${PLATFORM_CAMERA_STOP_SCRIPT}"
  fi
  rm -f "${PLATFORM_CAMERA_PID_FILE}"
}

start_new_platform_camera_process() {
  local log_file
  log_file="${PLATFORM_CAMERA_LOG_DIR}/platform_camera_$(date +%Y%m%d-%H%M%S).log"
  ln -sfn "$(basename "${log_file}")" "${PLATFORM_CAMERA_LOG_DIR}/platform_camera.log"
  setsid bash -lc "${PLATFORM_CAMERA_LAUNCH_CMD}" >"${log_file}" 2>&1 < /dev/null &
  echo "$!" >"${PLATFORM_CAMERA_PID_FILE}"
  log "pid=$(cat "${PLATFORM_CAMERA_PID_FILE}") log=${log_file}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --status) STATUS_MODE=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [[ "${STATUS_MODE}" == "1" ]]; then
  show_status
  exit 0
fi

log "starting platform camera; this is not the arm reading camera"
log "launch=${PLATFORM_CAMERA_LAUNCH_CMD}"
log "color_topic=${PLATFORM_CAMERA_COLOR_TOPIC}"
log "depth_topic=${PLATFORM_CAMERA_DEPTH_TOPIC}"

source_if_readable "${ROS_SETUP}"
source_if_readable "${ROS_WORKSPACE_SETUP}"

if [[ "${DRY_RUN}" == "1" ]]; then
  log "[dry-run] mkdir -p ${PLATFORM_CAMERA_RUNTIME_DIR} ${PLATFORM_CAMERA_LOG_DIR}"
  log "[dry-run] setsid bash -lc ${PLATFORM_CAMERA_LAUNCH_CMD}"
  exit 0
fi

mkdir -p "${PLATFORM_CAMERA_RUNTIME_DIR}" "${PLATFORM_CAMERA_LOG_DIR}"

if ! command -v ros2 >/dev/null 2>&1; then
  echo "ros2 command unavailable after sourcing setup files" >&2
  exit 1
fi

if already_has_publishers; then
  if platform_camera_ready; then
    log "platform camera topics already have publishers and required frames"
    show_status
    log_frame_health
    exit 0
  fi
  show_status
  log_frame_health
  stop_bad_platform_camera_process
fi

if [[ -s "${PLATFORM_CAMERA_PID_FILE}" ]]; then
  old_pid="$(cat "${PLATFORM_CAMERA_PID_FILE}" 2>/dev/null || true)"
  if [[ -n "${old_pid}" ]] && kill -0 "${old_pid}" 2>/dev/null; then
    log "existing platform camera process pid=${old_pid}; waiting for topics"
  else
    rm -f "${PLATFORM_CAMERA_PID_FILE}"
  fi
fi

if [[ ! -s "${PLATFORM_CAMERA_PID_FILE}" ]]; then
  start_new_platform_camera_process
fi

attempt=1
while ((attempt <= 2)); do
  deadline=$((SECONDS + PLATFORM_CAMERA_HEALTH_WAIT_SEC))
  while ((SECONDS < deadline)); do
    if platform_camera_ready; then
      show_status
      log_frame_health
      exit 0
    fi
    sleep 0.5
  done

  show_status
  log_frame_health
  if [[ "${PLATFORM_CAMERA_RESTART_ON_BAD_FRAME}" == "1" && "${attempt}" == "1" ]]; then
    stop_bad_platform_camera_process
    log "retrying platform camera start after bad frame health"
    start_new_platform_camera_process
    attempt=$((attempt + 1))
    continue
  fi
  break
done

show_status
log_frame_health
stop_bad_platform_camera_process
echo "platform camera did not publish required frames in ${PLATFORM_CAMERA_HEALTH_WAIT_SEC}s" >&2
exit 1
