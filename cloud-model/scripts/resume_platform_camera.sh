#!/usr/bin/env bash
set -euo pipefail

ROS_SETUP="${ROS_SETUP:-/opt/ros/humble/setup.bash}"
ROS_WORKSPACE_SETUP="${ROS_WORKSPACE_SETUP:-${HOME}/ros2/install/setup.bash}"
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-30}"
PLATFORM_CAMERA_COLOR_TOGGLE_SERVICE="${PLATFORM_CAMERA_COLOR_TOGGLE_SERVICE:-/camera/toggle_color}"
PLATFORM_CAMERA_DEPTH_TOGGLE_SERVICE="${PLATFORM_CAMERA_DEPTH_TOGGLE_SERVICE:-/camera/toggle_depth}"
PLATFORM_CAMERA_TOGGLE_TIMEOUT_SEC="${PLATFORM_CAMERA_TOGGLE_TIMEOUT_SEC:-8}"
PLATFORM_CAMERA_RESUME_VERIFY_FRAME="${PLATFORM_CAMERA_RESUME_VERIFY_FRAME:-1}"
PLATFORM_CAMERA_RESUME_REQUIRE_DEPTH_FRAME="${PLATFORM_CAMERA_RESUME_REQUIRE_DEPTH_FRAME:-0}"
PLATFORM_CAMERA_COLOR_TOPIC="${PLATFORM_CAMERA_COLOR_TOPIC:-/camera/color/image_raw}"
PLATFORM_CAMERA_DEPTH_TOPIC="${PLATFORM_CAMERA_DEPTH_TOPIC:-/camera/depth/image_raw}"
PLATFORM_CAMERA_FRAME_WAIT_SEC="${PLATFORM_CAMERA_FRAME_WAIT_SEC:-4}"

usage() {
  cat <<'EOF'
Usage: scripts/resume_platform_camera.sh

Resume platform Orbbec RGB/depth streams using existing ROS2 toggle services.
This expects the platform Orbbec node to still be running.
EOF
}

log() {
  printf '[platform_camera.resume] %s\n' "$*"
}

source_if_readable() {
  local file="$1"
  if [[ -r "${file}" ]]; then
    set +e
    set +u
    # shellcheck source=/dev/null
    source "${file}"
    local rc=$?
    set -e
    set -u
    return "${rc}"
  fi
  log "setup file not found, skipping: ${file}"
}

call_toggle() {
  local service="$1"
  local value="$2"
  local output
  log "toggle service=${service} value=${value}"
  output="$(timeout "${PLATFORM_CAMERA_TOGGLE_TIMEOUT_SEC}" ros2 service call "${service}" std_srvs/srv/SetBool "{data: ${value}}" 2>&1 || true)"
  printf '%s\n' "${output}" | tail -n 8
  if ! printf '%s\n' "${output}" | grep -q 'success=True' && ! printf '%s\n' "${output}" | grep -q 'Already ON'; then
    log "toggle_failed service=${service}"
    return 1
  fi
}

wait_for_frame() {
  local topic="$1"
  if timeout "${PLATFORM_CAMERA_FRAME_WAIT_SEC}" ros2 topic echo "${topic}" --once --field header >/dev/null 2>&1; then
    log "frame_ok topic=${topic}"
    return 0
  fi
  log "frame_timeout topic=${topic}"
  return 1
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

source_if_readable "${ROS_SETUP}" >/dev/null || true
source_if_readable "${ROS_WORKSPACE_SETUP}" >/dev/null || true

if ! command -v ros2 >/dev/null 2>&1; then
  echo "ros2 command unavailable after sourcing setup files" >&2
  exit 1
fi

call_toggle "${PLATFORM_CAMERA_DEPTH_TOGGLE_SERVICE}" true
call_toggle "${PLATFORM_CAMERA_COLOR_TOGGLE_SERVICE}" true

if [[ "${PLATFORM_CAMERA_RESUME_VERIFY_FRAME}" == "1" ]]; then
  wait_for_frame "${PLATFORM_CAMERA_COLOR_TOPIC}"
  if [[ "${PLATFORM_CAMERA_RESUME_REQUIRE_DEPTH_FRAME}" == "1" ]]; then
    wait_for_frame "${PLATFORM_CAMERA_DEPTH_TOPIC}"
  fi
fi

log "resumed"
