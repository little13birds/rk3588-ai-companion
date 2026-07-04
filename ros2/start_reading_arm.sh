#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROS_ROOT="${READING_ARM_ROS_ROOT:-$SCRIPT_DIR}"
RUNTIME_DIR="${READING_ARM_RUNTIME_DIR:-/tmp/reading_arm}"
LOG_DIR="${READING_ARM_LOG_DIR:-$ROS_ROOT/log/reading_arm}"
ROS_SETUP="${READING_ARM_ROS_SETUP:-/opt/ros/humble/setup.bash}"
WORKSPACE_SETUP="${READING_ARM_WORKSPACE_SETUP:-$ROS_ROOT/install/setup.bash}"
PROCESS_WAIT_SEC="${READING_ARM_PROCESS_WAIT_SEC:-1}"
HEALTH_URL="${READING_ARM_HEALTH_URL:-http://127.0.0.1:8642/book/status}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$RUNTIME_DIR" "$LOG_DIR"
exec 9>"$RUNTIME_DIR/lifecycle.lock"
if ! flock -n 9; then
    echo "Reading arm start/stop is already in progress." >&2
    exit 1
fi

if [[ ! -r "$ROS_SETUP" ]]; then
    echo "ROS setup not found: $ROS_SETUP" >&2
    exit 1
fi
if [[ ! -r "$WORKSPACE_SETUP" ]]; then
    echo "Workspace setup not found: $WORKSPACE_SETUP" >&2
    exit 1
fi

set +u
# shellcheck disable=SC1090
source "$ROS_SETUP"
# shellcheck disable=SC1090
source "$WORKSPACE_SETUP"
set -u
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-30}"

components=(roarm_driver servo_controller arm_agent)
started=()

find_reading_arm_residual_pids() {
    python3 - "$$" "$ROS_ROOT" <<'PY'
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
    if [[ -z "$pids" ]]; then
        return 0
    fi
    echo "Cleaning residual reading arm child processes: $pids"
    # shellcheck disable=SC2086
    kill -TERM $pids 2>/dev/null || true
    for _ in $(seq 1 20); do
        alive=""
        for pid in $pids; do
            kill -0 "$pid" 2>/dev/null && alive="$alive $pid"
        done
        [[ -z "$alive" ]] && break
        sleep 0.1
    done
    for pid in $pids; do
        kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null || true
    done
}

pid_file() {
    printf '%s/%s.pid\n' "$RUNTIME_DIR" "$1"
}

read_live_pid() {
    local file pid
    file="$(pid_file "$1")"
    [[ -s "$file" ]] || return 1
    pid="$(cat "$file")"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    printf '%s\n' "$pid"
}

stop_component() {
    local name="$1" file pid sid
    file="$(pid_file "$name")"
    if ! pid="$(read_live_pid "$name")"; then
        rm -f "$file" "$file.tmp"
        return 0
    fi

    sid="$(ps -o sid= -p "$pid" 2>/dev/null | tr -d ' ')"
    if [[ "$sid" != "$pid" ]]; then
        echo "Refusing to kill $name: PID $pid is not its session leader." >&2
        rm -f "$file" "$file.tmp"
        return 0
    fi

    kill -TERM -- "-$pid" 2>/dev/null || true
    if ! timeout "${READING_ARM_STOP_WAIT_SEC:-5}" \
        tail --pid="$pid" -f /dev/null 2>/dev/null; then
        kill -KILL -- "-$pid" 2>/dev/null || true
    fi
    rm -f "$file" "$file.tmp"
}

rollback() {
    local code=$?
    trap - ERR INT TERM
    echo "Startup failed; stopping components started in this run." >&2
    for ((i=${#started[@]} - 1; i >= 0; i--)); do
        stop_component "${started[$i]}"
    done
    exit "$code"
}
trap rollback ERR INT TERM

running=0
for name in "${components[@]}"; do
    if read_live_pid "$name" >/dev/null; then
        ((running += 1))
    else
        rm -f "$(pid_file "$name")" "$(pid_file "$name").tmp"
    fi
done

if ((running == ${#components[@]})); then
    trap - ERR INT TERM
    echo "Reading arm is already running."
    for name in "${components[@]}"; do
        echo "  $name PID $(read_live_pid "$name")"
    done
    exit 0
fi

if ((running > 0)); then
    echo "Partial previous startup detected; cleaning it before restart."
    for ((i=${#components[@]} - 1; i >= 0; i--)); do
        stop_component "${components[$i]}"
    done
fi

cleanup_residual_reading_arm_processes

start_component() {
    local name="$1"
    shift
    local file tmp_file log_file launcher pid
    file="$(pid_file "$name")"
    tmp_file="$file.tmp"
    log_file="$LOG_DIR/${name}_${TIMESTAMP}.log"
    rm -f "$file" "$tmp_file"
    ln -sfn "$(basename "$log_file")" "$LOG_DIR/$name.log"

    setsid bash -c '
        pid_file="$1"
        shift
        printf "%s\n" "$$" > "$pid_file.tmp"
        mv "$pid_file.tmp" "$pid_file"
        exec "$@"
    ' _ "$file" "$@" 9>&- >"$log_file" 2>&1 < /dev/null &
    launcher=$!

    for _ in {1..50}; do
        [[ -s "$file" ]] && break
        kill -0 "$launcher" 2>/dev/null || break
        sleep 0.02
    done
    [[ -s "$file" ]] || {
        echo "$name did not create its PID file. Log: $log_file" >&2
        return 1
    }

    pid="$(cat "$file")"
    started+=("$name")
    sleep "$PROCESS_WAIT_SEC"
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "$name exited during startup. Log: $log_file" >&2
        tail -n 20 "$log_file" >&2 || true
        return 1
    fi
    echo "Started $name (PID $pid, log $log_file)"
}

if [[ -n "${READING_ARM_DRIVER_CMD:-}" ]]; then
    start_component roarm_driver bash -lc "$READING_ARM_DRIVER_CMD"
else
    start_component roarm_driver ros2 run roarm_driver roarm_driver
fi

if [[ -n "${READING_ARM_SERVO_CMD:-}" ]]; then
    start_component servo_controller bash -lc "$READING_ARM_SERVO_CMD"
else
    start_component servo_controller \
        ros2 run face_track servo_controller --ros-args \
        --params-file "$ROS_ROOT/src/face_track/config/servo_params.yaml"
fi

if [[ -n "${READING_ARM_AGENT_CMD:-}" ]]; then
    start_component arm_agent bash -lc "$READING_ARM_AGENT_CMD"
else
    start_component arm_agent ros2 run face_track arm_agent
fi

if [[ "${READING_ARM_SKIP_HEALTHCHECK:-0}" != "1" ]]; then
    healthy=0
    for _ in {1..50}; do
        if curl -fsS --max-time 1 "$HEALTH_URL" >/dev/null 2>&1; then
            healthy=1
            break
        fi
        sleep 0.2
    done
    if ((healthy == 0)); then
        echo "arm_agent health check failed: $HEALTH_URL" >&2
        false
    fi
fi

trap - ERR INT TERM
echo "Reading arm started successfully."
echo "Status: curl -s $HEALTH_URL"
echo "Logs:  $LOG_DIR"
