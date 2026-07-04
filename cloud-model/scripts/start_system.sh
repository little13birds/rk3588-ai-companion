#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${CLOUD_MODEL_ROOT:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
LOG_DIR="${CLOUD_MODEL_LOG_DIR:-${ROOT}/logs}"
RUN_DIR="${CLOUD_MODEL_RUN_DIR:-${ROOT}/run}"
PID_FILE="${CLOUD_MODEL_PID_FILE:-${RUN_DIR}/cloud-model.pid}"
STOP_TIMEOUT_SEC="${CLOUD_MODEL_STOP_TIMEOUT_SEC:-15}"
FIX_AUDIO_SCRIPT="${FIX_AUDIO_SCRIPT:-/mnt/sdcard/reconstruct/fix_audio.sh}"
ROS_SETUP="${ROS_SETUP:-/opt/ros/humble/setup.bash}"
ROS_WORKSPACE_SETUP="${ROS_WORKSPACE_SETUP:-${HOME}/ros2/install/setup.bash}"
ROS_RGB_TOPIC="${ROS_RGB_TOPIC:-/camera/color/image_raw}"
READING_ARM_START_SCRIPT="${READING_ARM_START_SCRIPT:-${HOME}/ros2/start_reading_arm.sh}"
READING_ARM_STOP_SCRIPT="${READING_ARM_STOP_SCRIPT:-${HOME}/ros2/stop_reading_arm.sh}"
PLATFORM_CAMERA_START_SCRIPT="${PLATFORM_CAMERA_START_SCRIPT:-${SCRIPT_DIR}/start_platform_camera.sh}"
PLATFORM_CAMERA_STOP_SCRIPT="${PLATFORM_CAMERA_STOP_SCRIPT:-${SCRIPT_DIR}/stop_platform_camera.sh}"
PLATFORM_CAMERA_SUSPEND_SCRIPT="${PLATFORM_CAMERA_SUSPEND_SCRIPT:-${SCRIPT_DIR}/suspend_platform_camera.sh}"
PLATFORM_CAMERA_RESUME_SCRIPT="${PLATFORM_CAMERA_RESUME_SCRIPT:-${SCRIPT_DIR}/resume_platform_camera.sh}"

DRY_RUN=0
FOREGROUND=1
START_MAIN=1
RUN_AUDIO_FIX=1
START_PLATFORM_CAMERA=1
START_READING_ARM=0
CLI_DEBUG=0
DIALOG_DEBUG=0
VOICE_DIALOG_DEBUG=0
STOP_MODE=0
STATUS_MODE=0
PLATFORM_CAMERA_STOP_ON_STOP=0
ARM_STOP_ON_STOP=0
STOP_READING_ARM_BEFORE_PLATFORM_CAMERA=1

usage() {
  cat <<'EOF'
Usage: scripts/start_system.sh [options]

Start the cloud-model master safety/dashboard/scheduler stack.

Options:
  --with-arm          Start ~/ros2/start_reading_arm.sh before cloud-model.
  --cli-debug         Use the same startup path but run python3 debug_runtime.py instead of main.py.
  --dialog-debug      Run text-only LLM dialog debug with no ASR/TTS/camera/ROS/system calls.
  --voice-dialog-debug
                      Run voice ASR/TTS dialog debug with no camera/ROS/chassis/system calls.
  --no-platform-camera
                      Do not start the platform depth/RGB camera publisher.
  --auto-start-arm    Let runtime_scheduler auto-start the reading arm when entering reading mode. This is the default.
  --no-auto-start-arm
                      Do not auto-start the reading arm when entering reading mode.
  --stop-camera-on-stop
                      With --stop, also stop the platform depth/RGB camera.
  --stop-arm-on-stop  With --stop, also run ~/ros2/stop_reading_arm.sh.
  --keep-arm-before-platform
                      Do not stop a stale reading arm before starting the platform camera.
  --background        Start cloud-model in the background and write logs/PID under ./logs and ./run.
  --foreground        Start cloud-model in the foreground. This is the default.
  --stop              Stop the background cloud-model process recorded in ./run/cloud-model.pid.
  --status            Print background PID state and dashboard health probe.
  --no-main           Do setup only, then skip python3 main.py. Useful with --dry-run.
  --no-audio-fix      Skip /mnt/sdcard/reconstruct/fix_audio.sh.
  --no-safety         Export SAFETY_GUARD_ENABLED=0.
  --no-dashboard      Export DASHBOARD_ENABLED=0.
  --no-scheduler      Export RESOURCE_SCHEDULER_ENABLED=0.
  --dry-run           Print commands and effective environment without executing them.
  -h, --help          Show this help.

Common:
  ./scripts/start_system.sh
  ./scripts/start_system.sh --background
  ./scripts/start_system.sh --cli-debug
  ./scripts/start_system.sh --dialog-debug
  ./scripts/start_system.sh --voice-dialog-debug
  ./scripts/start_system.sh --with-arm
  ./scripts/start_system.sh --no-platform-camera
  ./scripts/start_system.sh --status
  ./scripts/start_system.sh --stop
EOF
}

log() {
  printf '[start_system] %s\n' "$*"
}

run_cmd() {
  if [[ "${DRY_RUN}" == "1" ]]; then
    printf '[dry-run] %q' "$1"
    shift || true
    for arg in "$@"; do
      printf ' %q' "$arg"
    done
    printf '\n'
  else
    "$@"
  fi
}

source_if_readable() {
  local file="$1"
  if [[ -r "${file}" ]]; then
    if [[ "${DRY_RUN}" == "1" ]]; then
      log "[dry-run] source ${file}"
    else
      # shellcheck source=/dev/null
      set +e
      set +u
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

probe_dashboard() {
  python3 - <<'PY'
import os
import urllib.error
import urllib.request

port = os.environ.get("DASHBOARD_PORT", "8080")
url = f"http://127.0.0.1:{port}/api/health"
try:
    with urllib.request.urlopen(url, timeout=1.5) as resp:
        print(f"dashboard: {url} -> HTTP {resp.status}")
except Exception as exc:
    print(f"dashboard: {url} -> unavailable ({type(exc).__name__})")
PY
}

probe_ros_camera() {
  source_if_readable "${ROS_SETUP}" >/dev/null || true
  source_if_readable "${ROS_WORKSPACE_SETUP}" >/dev/null || true
  if ! command -v ros2 >/dev/null 2>&1; then
    log "ros2 command unavailable; cannot check ${ROS_RGB_TOPIC}"
    return
  fi
  local info
  info="$(timeout 5 ros2 topic info -v "${ROS_RGB_TOPIC}" 2>&1 || true)"
  local publishers
  publishers="$(printf '%s\n' "${info}" | awk -F': ' '/Publisher count/ {print $2; exit}')"
  local subscribers
  subscribers="$(printf '%s\n' "${info}" | awk -F': ' '/Subscription count/ {print $2; exit}')"
  publishers="${publishers:-unknown}"
  subscribers="${subscribers:-unknown}"
  log "ros rgb topic ${ROS_RGB_TOPIC}: publishers=${publishers} subscribers=${subscribers}"
  if [[ "${publishers}" == "0" ]]; then
    log "ros rgb has no publisher; safety_guard can run but will not receive frames"
  fi
}

check_reading_arm_prepare_capability() {
  local base_url="${ARM_AGENT_URL:-http://127.0.0.1:8642}"
  if [[ "${DRY_RUN}" == "1" ]]; then
    log "[dry-run] reading arm prepare capability check: GET ${base_url}/book/status contains prepare_complete; reading mode will use /reading/prepare?timeout=0.5+"
    return 0
  fi
  python3 - "${base_url}" <<'PYCHECK'
import json
import sys
import urllib.request

base_url = sys.argv[1].rstrip("/")
try:
    with urllib.request.urlopen(base_url + "/book/status", timeout=2.0) as resp:
        data = json.loads(resp.read().decode("utf-8"))
except Exception as exc:
    print(f"[start_system] reading arm prepare capability check failed: {type(exc).__name__}: {exc}")
    sys.exit(1)

missing = [key for key in ("preparing", "prepare_complete") if key not in data]
if missing:
    print(
        "[start_system] reading arm prepare capability missing: "
        + ",".join(missing)
        + "; an old arm_agent/servo_controller may still be running. "
        + "Run '~/ros2/stop_reading_arm.sh && ~/ros2/start_reading_arm.sh' or restart with --stop-arm-on-stop."
    )
    sys.exit(1)
print("[start_system] reading arm prepare capability ok")
PYCHECK
}

find_reading_arm_residual_pids() {
  python3 - "$$" "${HOME}/ros2" <<'PY'
import os
import sys

script_pid = int(sys.argv[1])
ros_root = os.path.realpath(sys.argv[2])
skip = {os.getpid(), os.getppid(), script_pid}
targets = {
    os.path.join(ros_root, "install/face_track/lib/face_track/arm_agent"),
    os.path.join(ros_root, "install/face_track/lib/face_track/servo_controller"),
    os.path.join(ros_root, "install/roarm_driver/lib/roarm_driver/roarm_driver"),
}

for name in os.listdir("/proc"):
    if not name.isdigit():
        continue
    pid = int(name)
    if pid in skip:
        continue
    try:
        raw = open(f"/proc/{pid}/cmdline", "rb").read()
    except OSError:
        continue
    args = [part.decode("utf-8", "ignore") for part in raw.split(b"\0") if part]
    if not args:
        continue
    for arg in args:
        candidate = os.path.realpath(arg)
        if candidate in targets:
            print(pid)
            break
PY
}

cleanup_residual_reading_arm_processes() {
  local pids alive pid
  pids="$(find_reading_arm_residual_pids | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
  if [[ -z "${pids}" ]]; then
    return 0
  fi
  log "cleaning residual reading arm child processes: pids=${pids}"
  if [[ "${DRY_RUN}" == "1" ]]; then
    log "[dry-run] kill ${pids}"
    return 0
  fi
  # shellcheck disable=SC2086
  kill -TERM ${pids} 2>/dev/null || true
  for _ in $(seq 1 20); do
    alive=""
    for pid in ${pids}; do
      kill -0 "${pid}" 2>/dev/null && alive="${alive} ${pid}"
    done
    [[ -z "${alive}" ]] && return 0
    sleep 0.1
  done
  for pid in ${pids}; do
    kill -0 "${pid}" 2>/dev/null && kill -KILL "${pid}" 2>/dev/null || true
  done
}

find_audio_residual_pids() {
  python3 - "$$" <<'PY'
import os
import sys

script_pid = int(sys.argv[1])
skip = {os.getpid(), os.getppid(), script_pid}
target_names = {"aplay", "arecord", "paplay", "ffplay", "speaker-test"}

for name in os.listdir("/proc"):
    if not name.isdigit():
        continue
    pid = int(name)
    if pid in skip:
        continue
    try:
        raw = open(f"/proc/{pid}/cmdline", "rb").read()
    except OSError:
        continue
    args = [part.decode("utf-8", "ignore") for part in raw.split(b"\0") if part]
    if not args:
        continue
    exe_name = os.path.basename(args[0])
    if exe_name in target_names:
        print(pid)
PY
}

cleanup_residual_audio_processes() {
  local pids alive pid
  pids="$(find_audio_residual_pids | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
  if [[ -z "${pids}" ]]; then
    return 0
  fi
  log "cleaning residual audio playback/capture processes: pids=${pids}"
  if [[ "${DRY_RUN}" == "1" ]]; then
    log "[dry-run] kill ${pids}"
    return 0
  fi
  # shellcheck disable=SC2086
  kill -TERM ${pids} 2>/dev/null || true
  for _ in $(seq 1 15); do
    alive=""
    for pid in ${pids}; do
      kill -0 "${pid}" 2>/dev/null && alive="${alive} ${pid}"
    done
    [[ -z "${alive}" ]] && return 0
    sleep 0.1
  done
  for pid in ${pids}; do
    kill -0 "${pid}" 2>/dev/null && kill -KILL "${pid}" 2>/dev/null || true
  done
}

stop_stale_reading_arm_before_platform_camera() {
  if [[ "${STOP_READING_ARM_BEFORE_PLATFORM_CAMERA}" != "1" ]]; then
    return 0
  fi
  if [[ "${START_PLATFORM_CAMERA}" != "1" || "${START_READING_ARM}" == "1" ]]; then
    return 0
  fi
  if [[ ! -x "${READING_ARM_STOP_SCRIPT}" ]]; then
    log "reading arm stop script not executable, cannot release stale arm before platform camera: ${READING_ARM_STOP_SCRIPT}"
    return 0
  fi
  log "stop stale reading arm before platform camera startup"
  run_cmd "${READING_ARM_STOP_SCRIPT}"
  cleanup_residual_reading_arm_processes
}

find_main_pids() {
  python3 - "${ROOT}" "$$" <<'PY'
import os
import sys

root = os.path.realpath(sys.argv[1])
script_pid = int(sys.argv[2])
skip = {os.getpid(), os.getppid(), script_pid}
target = os.path.join(root, "main.py")
for name in os.listdir("/proc"):
    if not name.isdigit():
        continue
    pid = int(name)
    if pid in skip:
        continue
    try:
        raw = open(f"/proc/{pid}/cmdline", "rb").read()
        args = [part.decode("utf-8", "ignore") for part in raw.split(b"\0") if part]
    except OSError:
        continue
    if len(args) < 2:
        continue
    exe = os.path.basename(args[0])
    if "python" not in exe:
        continue
    main_args = [arg for arg in args[1:] if arg.endswith("main.py")]
    if not main_args:
        continue
    try:
        cwd = os.path.realpath(os.readlink(f"/proc/{pid}/cwd"))
    except OSError:
        cwd = ""
    matches = False
    for arg in main_args:
        if os.path.isabs(arg) and os.path.realpath(arg) == target:
            matches = True
        elif cwd == root and arg == "main.py":
            matches = True
    if matches:
        print(pid)
PY
}

check_existing_main_processes() {
  local pids
  pids="$(find_main_pids | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
  if [[ -n "${pids}" ]]; then
    log "residual cloud-model process detected: pids=${pids}"
    log "run './scripts/start_system.sh --stop' or stop the process before starting another instance"
    return 1
  fi
  return 0
}

stop_residual_main_processes() {
  local pids
  pids="$(find_main_pids | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
  if [[ -z "${pids}" ]]; then
    log "no residual cloud-model process found"
    return 0
  fi
  log "stopping residual cloud-model process pids=${pids}"
  if [[ "${DRY_RUN}" == "1" ]]; then
    log "[dry-run] kill ${pids}"
    return 0
  fi
  # shellcheck disable=SC2086
  kill ${pids} 2>/dev/null || true
  for _ in $(seq 1 30); do
    local alive=""
    for pid in ${pids}; do
      kill -0 "${pid}" 2>/dev/null && alive="${alive} ${pid}"
    done
    [[ -z "${alive}" ]] && return 0
    sleep 0.2
  done
  for pid in ${pids}; do
    kill -0 "${pid}" 2>/dev/null && kill -9 "${pid}" 2>/dev/null || true
  done
}

show_status() {
  if [[ -f "${PID_FILE}" ]]; then
    pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      log "cloud-model is running pid=${pid}"
    else
      log "stale pid file: ${PID_FILE}"
    fi
  else
    log "no pid file: ${PID_FILE}"
  fi
  residual="$(find_main_pids | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
  if [[ -n "${residual}" ]]; then
    log "residual cloud-model process detected: pids=${residual}"
  else
    log "no residual cloud-model process found"
  fi
  probe_dashboard
  probe_ros_camera
  if [[ -x "${PLATFORM_CAMERA_START_SCRIPT}" ]]; then
    "${PLATFORM_CAMERA_START_SCRIPT}" --status || true
  fi
}

stop_background() {
  if [[ -f "${PID_FILE}" ]]; then
    pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      log "stopping cloud-model pid=${pid}"
      if [[ "${DRY_RUN}" == "1" ]]; then
        log "[dry-run] kill ${pid}"
      else
        log "sending INT for graceful cleanup pid=${pid}"
        kill -INT "${pid}"
        local stop_polls
        stop_polls="$(python3 - "${STOP_TIMEOUT_SEC}" <<'PY'
import math
import sys

try:
    timeout = float(sys.argv[1])
except (TypeError, ValueError):
    timeout = 15.0
print(max(1, int(math.ceil(timeout / 0.2))))
PY
)"
        for _ in $(seq 1 "${stop_polls}"); do
          kill -0 "${pid}" 2>/dev/null || break
          sleep 0.2
        done
        if kill -0 "${pid}" 2>/dev/null; then
          log "pid ${pid} did not exit after ${STOP_TIMEOUT_SEC}s; sending TERM again"
          kill -TERM "${pid}" || true
        fi
      fi
    else
      log "cloud-model pid is not running"
    fi
    [[ "${DRY_RUN}" == "1" ]] || rm -f "${PID_FILE}"
  else
    log "cloud-model pid file not found"
  fi
  stop_residual_main_processes
  if [[ "${ARM_STOP_ON_STOP}" == "1" ]]; then
    if [[ -x "${READING_ARM_STOP_SCRIPT}" ]]; then
      run_cmd "${READING_ARM_STOP_SCRIPT}"
    else
      log "arm stop script not executable: ${READING_ARM_STOP_SCRIPT}"
    fi
  fi
  if [[ "${PLATFORM_CAMERA_STOP_ON_STOP}" == "1" ]]; then
    if [[ -x "${PLATFORM_CAMERA_STOP_SCRIPT}" ]]; then
      "${PLATFORM_CAMERA_STOP_SCRIPT}"
    else
      log "platform camera stop script not executable: ${PLATFORM_CAMERA_STOP_SCRIPT}"
    fi
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-arm) START_READING_ARM=1 ;;
    --cli-debug) CLI_DEBUG=1 ;;
    --dialog-debug)
      DIALOG_DEBUG=1
      VOICE_DIALOG_DEBUG=0
      CLI_DEBUG=0
      RUN_AUDIO_FIX=0
      START_PLATFORM_CAMERA=0
      START_READING_ARM=0
      export SAFETY_GUARD_ENABLED=0
      export DASHBOARD_ENABLED=0
      export RESOURCE_SCHEDULER_ENABLED=0
      ;;
    --voice-dialog-debug)
      VOICE_DIALOG_DEBUG=1
      DIALOG_DEBUG=0
      CLI_DEBUG=0
      START_PLATFORM_CAMERA=0
      START_READING_ARM=0
      export SAFETY_GUARD_ENABLED=0
      export DASHBOARD_ENABLED=0
      export RESOURCE_SCHEDULER_ENABLED=0
      ;;
    --no-platform-camera) START_PLATFORM_CAMERA=0 ;;
    --auto-start-arm) export SCHEDULER_AUTO_START_READING_ARM=1 ;;
    --no-auto-start-arm) export SCHEDULER_AUTO_START_READING_ARM=0 ;;
    --stop-camera-on-stop) PLATFORM_CAMERA_STOP_ON_STOP=1 ;;
    --stop-arm-on-stop) ARM_STOP_ON_STOP=1 ;;
    --keep-arm-before-platform) STOP_READING_ARM_BEFORE_PLATFORM_CAMERA=0 ;;
    --background) FOREGROUND=0 ;;
    --foreground) FOREGROUND=1 ;;
    --stop) STOP_MODE=1 ;;
    --status) STATUS_MODE=1 ;;
    --no-main) START_MAIN=0 ;;
    --no-audio-fix) RUN_AUDIO_FIX=0 ;;
    --no-safety) export SAFETY_GUARD_ENABLED=0 ;;
    --no-dashboard) export DASHBOARD_ENABLED=0 ;;
    --no-scheduler) export RESOURCE_SCHEDULER_ENABLED=0 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

export DASHBOARD_ENABLED="${DASHBOARD_ENABLED:-1}"
export DASHBOARD_HOST="${DASHBOARD_HOST:-0.0.0.0}"
export DASHBOARD_PORT="${DASHBOARD_PORT:-8080}"
export DASHBOARD_CHASSIS_CONTROL_ENABLED="${DASHBOARD_CHASSIS_CONTROL_ENABLED:-1}"
export EYE_GUI_ENABLED="${EYE_GUI_ENABLED:-1}"
export RESOURCE_SCHEDULER_ENABLED="${RESOURCE_SCHEDULER_ENABLED:-1}"
export SCHEDULER_AUTO_START_READING_ARM="${SCHEDULER_AUTO_START_READING_ARM:-1}"
export SCHEDULER_READING_PAUSES_SAFETY="${SCHEDULER_READING_PAUSES_SAFETY:-1}"
export SCHEDULER_READING_STOPS_PLATFORM_CAMERA="${SCHEDULER_READING_STOPS_PLATFORM_CAMERA:-1}"
export SCHEDULER_PLATFORM_CAMERA_RELEASE_MODE="${SCHEDULER_PLATFORM_CAMERA_RELEASE_MODE:-suspend}"
export SCHEDULER_PLATFORM_CAMERA_SUSPEND_FALLBACK_TO_STOP="${SCHEDULER_PLATFORM_CAMERA_SUSPEND_FALLBACK_TO_STOP:-1}"
export SCHEDULER_REQUIRE_FRAME_HEALTH="${SCHEDULER_REQUIRE_FRAME_HEALTH:-1}"
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-30}"
export PLATFORM_CAMERA_START_SCRIPT
export PLATFORM_CAMERA_STOP_SCRIPT
export PLATFORM_CAMERA_SUSPEND_SCRIPT
export PLATFORM_CAMERA_RESUME_SCRIPT
export SAFETY_GUARD_ENABLED="${SAFETY_GUARD_ENABLED:-1}"
export START_PLATFORM_CAMERA
export START_READING_ARM

if [[ "${STOP_MODE}" == "1" ]]; then
  stop_background
  exit 0
fi

if [[ "${STATUS_MODE}" == "1" ]]; then
  show_status
  exit 0
fi

log "root=${ROOT}"
log "START_PLATFORM_CAMERA=${START_PLATFORM_CAMERA}"
log "START_READING_ARM=${START_READING_ARM}"
log "CLI_DEBUG=${CLI_DEBUG}"
log "DIALOG_DEBUG=${DIALOG_DEBUG}"
log "VOICE_DIALOG_DEBUG=${VOICE_DIALOG_DEBUG}"
log "SAFETY_GUARD_ENABLED=${SAFETY_GUARD_ENABLED}"
log "DASHBOARD_ENABLED=${DASHBOARD_ENABLED}"
log "DASHBOARD_PORT=${DASHBOARD_PORT}"
log "DASHBOARD_CHASSIS_CONTROL_ENABLED=${DASHBOARD_CHASSIS_CONTROL_ENABLED}"
log "EYE_GUI_ENABLED=${EYE_GUI_ENABLED}"
log "RESOURCE_SCHEDULER_ENABLED=${RESOURCE_SCHEDULER_ENABLED}"
log "SCHEDULER_AUTO_START_READING_ARM=${SCHEDULER_AUTO_START_READING_ARM}"
log "SCHEDULER_READING_PAUSES_SAFETY=${SCHEDULER_READING_PAUSES_SAFETY}"
log "SCHEDULER_READING_STOPS_PLATFORM_CAMERA=${SCHEDULER_READING_STOPS_PLATFORM_CAMERA}"
log "SCHEDULER_PLATFORM_CAMERA_RELEASE_MODE=${SCHEDULER_PLATFORM_CAMERA_RELEASE_MODE}"
log "SCHEDULER_PLATFORM_CAMERA_SUSPEND_FALLBACK_TO_STOP=${SCHEDULER_PLATFORM_CAMERA_SUSPEND_FALLBACK_TO_STOP}"
log "SCHEDULER_REQUIRE_FRAME_HEALTH=${SCHEDULER_REQUIRE_FRAME_HEALTH}"
log "ROS_DOMAIN_ID=${ROS_DOMAIN_ID}"
log "STOP_READING_ARM_BEFORE_PLATFORM_CAMERA=${STOP_READING_ARM_BEFORE_PLATFORM_CAMERA}"

if [[ "${DIALOG_DEBUG}" != "1" && "${VOICE_DIALOG_DEBUG}" != "1" ]]; then
  source_if_readable "${ROS_SETUP}"
  source_if_readable "${ROS_WORKSPACE_SETUP}"
fi

ENTRYPOINT="main.py"
if [[ "${CLI_DEBUG}" == "1" ]]; then
  ENTRYPOINT="debug_runtime.py"
elif [[ "${DIALOG_DEBUG}" == "1" ]]; then
  ENTRYPOINT="dialog_debug.py"
elif [[ "${VOICE_DIALOG_DEBUG}" == "1" ]]; then
  ENTRYPOINT="voice_dialog_debug.py"
fi

if [[ ! -f "${ROOT}/${ENTRYPOINT}" ]]; then
  echo "${ENTRYPOINT} not found under ${ROOT}" >&2
  exit 1
fi

if [[ "${DRY_RUN}" == "1" ]]; then
  log "[dry-run] mkdir -p ${LOG_DIR} ${RUN_DIR}"
else
  mkdir -p "${LOG_DIR}" "${RUN_DIR}"
fi

cleanup_residual_audio_processes

if [[ "${RUN_AUDIO_FIX}" == "1" ]]; then
  if [[ -x "${FIX_AUDIO_SCRIPT}" ]]; then
    run_cmd "${FIX_AUDIO_SCRIPT}"
  else
    log "audio fix script not executable, skipping: ${FIX_AUDIO_SCRIPT}"
  fi
fi

stop_stale_reading_arm_before_platform_camera

if [[ "${START_PLATFORM_CAMERA}" == "1" ]]; then
  if [[ -x "${PLATFORM_CAMERA_START_SCRIPT}" ]]; then
    if [[ "${DRY_RUN}" == "1" ]]; then
      run_cmd "${PLATFORM_CAMERA_START_SCRIPT}" --dry-run
    else
      "${PLATFORM_CAMERA_START_SCRIPT}"
    fi
  else
    log "platform camera start script not executable, skipping: ${PLATFORM_CAMERA_START_SCRIPT}"
  fi
fi

if [[ "${START_READING_ARM}" == "1" ]]; then
  if [[ -x "${READING_ARM_START_SCRIPT}" ]]; then
    run_cmd "${READING_ARM_START_SCRIPT}"
    check_reading_arm_prepare_capability
  else
    log "reading arm start script not executable, skipping: ${READING_ARM_START_SCRIPT}"
  fi
fi

cd "${ROOT}"

if [[ "${START_MAIN}" == "0" ]]; then
  log "skip main startup (--no-main)"
  if [[ "${DIALOG_DEBUG}" == "1" ]]; then
    log "would run: python3 dialog_debug.py"
  elif [[ "${VOICE_DIALOG_DEBUG}" == "1" ]]; then
    log "would run: python3 voice_dialog_debug.py"
  elif [[ "${CLI_DEBUG}" == "1" ]]; then
    log "would run: python3 debug_runtime.py"
  else
    log "would run: python3 main.py"
  fi
  exit 0
fi

if [[ "${DIALOG_DEBUG}" != "1" ]]; then
  check_existing_main_processes
fi

if [[ "${CLI_DEBUG}" == "1" && ! -f "${ROOT}/debug_runtime.py" ]]; then
  echo "debug_runtime.py not found under ${ROOT}" >&2
  exit 1
fi

if [[ "${DIALOG_DEBUG}" == "1" && "${FOREGROUND}" != "1" ]]; then
  log "dialog debug is interactive; use --foreground or omit --background"
  exit 2
fi

if [[ "${VOICE_DIALOG_DEBUG}" == "1" && "${FOREGROUND}" != "1" ]]; then
  log "voice dialog debug is interactive; use --foreground or omit --background"
  exit 2
fi

if [[ "${FOREGROUND}" == "1" ]]; then
  if [[ "${DIALOG_DEBUG}" == "1" ]]; then
    log "starting cloud-model dialog debug in foreground"
    if [[ "${DRY_RUN}" == "1" ]]; then
      log "[dry-run] python3 dialog_debug.py"
    else
      exec python3 dialog_debug.py
    fi
  fi
  if [[ "${VOICE_DIALOG_DEBUG}" == "1" ]]; then
    log "starting cloud-model voice dialog debug in foreground"
    if [[ "${DRY_RUN}" == "1" ]]; then
      log "[dry-run] python3 voice_dialog_debug.py"
    else
      exec python3 voice_dialog_debug.py
    fi
  fi
  if [[ "${CLI_DEBUG}" == "1" ]]; then
    log "starting cloud-model CLI debug in foreground"
    if [[ "${DRY_RUN}" == "1" ]]; then
      log "[dry-run] python3 debug_runtime.py"
    else
      exec python3 debug_runtime.py
    fi
  fi
  log "starting cloud-model in foreground"
  if [[ "${DRY_RUN}" == "1" ]]; then
    log "[dry-run] python3 main.py"
  else
    exec python3 main.py
  fi
else
  if [[ -f "${PID_FILE}" ]] && kill -0 "$(cat "${PID_FILE}" 2>/dev/null)" 2>/dev/null; then
    log "cloud-model already running pid=$(cat "${PID_FILE}")"
    exit 0
  fi
  ts="$(date +%Y%m%d-%H%M%S)"
  log_file="${LOG_DIR}/cloud-model_${ts}.log"
  if [[ "${CLI_DEBUG}" == "1" ]]; then
    log "CLI debug is interactive; use --foreground or omit --background"
    exit 2
  fi
  log "starting cloud-model in background, log=${log_file}"
  if [[ "${DRY_RUN}" == "1" ]]; then
    log "[dry-run] nohup python3 main.py > ${log_file} 2>&1 &"
  else
    nohup python3 main.py >"${log_file}" 2>&1 &
    echo "$!" >"${PID_FILE}"
    log "pid=$(cat "${PID_FILE}")"
    log "dashboard=http://$(hostname -I 2>/dev/null | awk '{print $1}'):${DASHBOARD_PORT}"
  fi
fi
