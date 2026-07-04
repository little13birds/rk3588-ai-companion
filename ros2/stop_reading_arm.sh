#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROS_ROOT="${READING_ARM_ROS_ROOT:-$SCRIPT_DIR}"
RUNTIME_DIR="${READING_ARM_RUNTIME_DIR:-/tmp/reading_arm}"
LOG_DIR="${READING_ARM_LOG_DIR:-$ROS_ROOT/log/reading_arm}"
STOP_WAIT_SEC="${READING_ARM_STOP_WAIT_SEC:-5}"

mkdir -p "$RUNTIME_DIR" "$LOG_DIR"
exec 9>"$RUNTIME_DIR/lifecycle.lock"
if ! flock -n 9; then
    echo "Reading arm start/stop is already in progress." >&2
    exit 1
fi

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

stop_component() {
    local name="$1" file pid sid
    file="$(pid_file "$name")"
    if [[ ! -s "$file" ]]; then
        rm -f "$file" "$file.tmp"
        echo "$name is not running."
        return 0
    fi

    pid="$(cat "$file")"
    if [[ ! "$pid" =~ ^[0-9]+$ ]] || ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$file" "$file.tmp"
        echo "$name had a stale PID file."
        return 0
    fi

    sid="$(ps -o sid= -p "$pid" 2>/dev/null | tr -d ' ')"
    if [[ "$sid" != "$pid" ]]; then
        echo "Refusing to kill $name: PID $pid is not its session leader." >&2
        rm -f "$file" "$file.tmp"
        return 0
    fi

    echo "Stopping $name (PID $pid)..."
    kill -TERM -- "-$pid" 2>/dev/null || true
    if ! timeout "$STOP_WAIT_SEC" tail --pid="$pid" -f /dev/null 2>/dev/null; then
        echo "$name did not stop in ${STOP_WAIT_SEC}s; sending SIGKILL."
        kill -KILL -- "-$pid" 2>/dev/null || true
    fi
    rm -f "$file" "$file.tmp"
}

stop_component arm_agent
stop_component servo_controller
stop_component roarm_driver
cleanup_residual_reading_arm_processes

echo "Reading arm stopped."
