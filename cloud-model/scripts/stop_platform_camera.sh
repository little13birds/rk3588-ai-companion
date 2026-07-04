#!/usr/bin/env bash
set -euo pipefail

PLATFORM_CAMERA_RUNTIME_DIR="${PLATFORM_CAMERA_RUNTIME_DIR:-/tmp/platform_camera}"
PLATFORM_CAMERA_PID_FILE="${PLATFORM_CAMERA_PID_FILE:-${PLATFORM_CAMERA_RUNTIME_DIR}/platform_camera.pid}"
STOP_WAIT_SEC="${PLATFORM_CAMERA_STOP_WAIT_SEC:-5}"
PLATFORM_CAMERA_STOP_FALLBACK="${PLATFORM_CAMERA_STOP_FALLBACK:-1}"

usage() {
  cat <<'EOF'
Usage: scripts/stop_platform_camera.sh [options]

Stop the platform depth/RGB ROS camera process started by start_platform_camera.sh.
This is the chassis/platform camera, not the arm reading camera.

Options:
  -h, --help    Show this help.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

log() {
  printf '[platform_camera] %s\n' "$*"
}

fallback_pids() {
  {
    pgrep -f 'ros2 launch orbbec_camera (orbbec_camera\.launch\.py|astra\.launch\.xml)' 2>/dev/null || true
    pgrep -f '/orbbec_camera/.*/orbbec_camera_node|orbbec_camera_node' 2>/dev/null || true
  } | awk -v self="$$" '$1 != self {print $1}' | sort -nu
}

stop_pid_list() {
  local pids="$1"
  [[ -n "${pids}" ]] || return 0
  log "fallback stopping platform camera pids=${pids}"
  # shellcheck disable=SC2086
  kill -TERM ${pids} 2>/dev/null || true
  local alive
  for _ in $(seq 1 $((STOP_WAIT_SEC * 5))); do
    alive=""
    for pid in ${pids}; do
      kill -0 "${pid}" 2>/dev/null && alive="${alive} ${pid}"
    done
    [[ -z "${alive}" ]] && return 0
    sleep 0.2
  done
  log "fallback platform camera processes did not stop in ${STOP_WAIT_SEC}s; sending SIGKILL"
  # shellcheck disable=SC2086
  kill -KILL ${pids} 2>/dev/null || true
}

stop_fallback_platform_camera() {
  if [[ "${PLATFORM_CAMERA_STOP_FALLBACK}" != "1" ]]; then
    log "fallback stop disabled"
    return 0
  fi
  local pids
  pids="$(fallback_pids | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
  if [[ -z "${pids}" ]]; then
    log "fallback found no matching platform camera process"
    return 0
  fi
  stop_pid_list "${pids}"
}

if [[ ! -s "${PLATFORM_CAMERA_PID_FILE}" ]]; then
  log "platform camera pid file not found"
  stop_fallback_platform_camera
  exit 0
fi

pid="$(cat "${PLATFORM_CAMERA_PID_FILE}" 2>/dev/null || true)"
if [[ ! "${pid}" =~ ^[0-9]+$ ]] || ! kill -0 "${pid}" 2>/dev/null; then
  rm -f "${PLATFORM_CAMERA_PID_FILE}"
  log "platform camera had a stale pid file"
  stop_fallback_platform_camera
  exit 0
fi

sid="$(ps -o sid= -p "${pid}" 2>/dev/null | tr -d ' ')"
if [[ "${sid}" != "${pid}" ]]; then
  log "refusing to kill pid=${pid}: not its session leader"
  rm -f "${PLATFORM_CAMERA_PID_FILE}"
  stop_fallback_platform_camera
  exit 0
fi

log "stopping platform camera pid=${pid}"
kill -TERM -- "-${pid}" 2>/dev/null || true
if ! timeout "${STOP_WAIT_SEC}" tail --pid="${pid}" -f /dev/null 2>/dev/null; then
  log "platform camera did not stop in ${STOP_WAIT_SEC}s; sending SIGKILL"
  kill -KILL -- "-${pid}" 2>/dev/null || true
fi
rm -f "${PLATFORM_CAMERA_PID_FILE}"
log "platform camera stopped"
