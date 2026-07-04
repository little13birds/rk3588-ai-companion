#!/usr/bin/env bash
set -euo pipefail

ROS_SETUP="${ROS_SETUP:-/opt/ros/humble/setup.bash}"
ROS_WORKSPACE_SETUP="${ROS_WORKSPACE_SETUP:-${HOME}/ros2/install/setup.bash}"
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-30}"
PLATFORM_CAMERA_COLOR_TOGGLE_SERVICE="${PLATFORM_CAMERA_COLOR_TOGGLE_SERVICE:-/camera/toggle_color}"
PLATFORM_CAMERA_DEPTH_TOGGLE_SERVICE="${PLATFORM_CAMERA_DEPTH_TOGGLE_SERVICE:-/camera/toggle_depth}"
PLATFORM_CAMERA_TOGGLE_TIMEOUT_SEC="${PLATFORM_CAMERA_TOGGLE_TIMEOUT_SEC:-8}"
PLATFORM_CAMERA_SUSPEND_VERIFY_NO_FRAME="${PLATFORM_CAMERA_SUSPEND_VERIFY_NO_FRAME:-0}"
PLATFORM_CAMERA_COLOR_TOPIC="${PLATFORM_CAMERA_COLOR_TOPIC:-/camera/color/image_raw}"
PLATFORM_CAMERA_DEPTH_TOPIC="${PLATFORM_CAMERA_DEPTH_TOPIC:-/camera/depth/image_raw}"
PLATFORM_CAMERA_FRAME_WAIT_SEC="${PLATFORM_CAMERA_FRAME_WAIT_SEC:-2}"

usage() {
  cat <<'EOF'
Usage: scripts/suspend_platform_camera.sh

Suspend platform Orbbec RGB/depth streams using existing ROS2 toggle services.
This keeps the Orbbec node alive while stopping active color/depth frames.
EOF
}

log() {
  printf '[platform_camera.suspend] %s\n' "$*"
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
  if ! printf '%s\n' "${output}" | grep -q 'success=True' && ! printf '%s\n' "${output}" | grep -q 'Already OFF'; then
    log "toggle_failed service=${service}"
    return 1
  fi
}

topic_has_frame() {
  local topic="$1"
  timeout "${PLATFORM_CAMERA_FRAME_WAIT_SEC}" ros2 topic echo "${topic}" --once --field header >/dev/null 2>&1
}

verify_no_frame() {
  local topic="$1"
  if topic_has_frame "${topic}"; then
    log "unexpected_frame topic=${topic}"
    return 1
  fi
  log "no_frame topic=${topic}"
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

call_toggle "${PLATFORM_CAMERA_COLOR_TOGGLE_SERVICE}" false
call_toggle "${PLATFORM_CAMERA_DEPTH_TOGGLE_SERVICE}" false

if [[ "${PLATFORM_CAMERA_SUSPEND_VERIFY_NO_FRAME}" == "1" ]]; then
  verify_no_frame "${PLATFORM_CAMERA_COLOR_TOPIC}"
  verify_no_frame "${PLATFORM_CAMERA_DEPTH_TOPIC}"
fi

log "suspended"
