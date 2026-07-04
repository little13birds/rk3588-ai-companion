#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
START_SCRIPT="$SCRIPT_DIR/start_reading_arm.sh"
STOP_SCRIPT="$SCRIPT_DIR/stop_reading_arm.sh"
TMP_ROOT="$(mktemp -d)"
RUNTIME_DIR="$TMP_ROOT/run"
LOG_DIR="$TMP_ROOT/log"
FAKE_ROS_SETUP="$TMP_ROOT/ros_setup.bash"
FAKE_WORKSPACE_SETUP="$TMP_ROOT/workspace_setup.bash"

cleanup() {
    if [[ -x "$STOP_SCRIPT" ]]; then
        env \
            READING_ARM_RUNTIME_DIR="$RUNTIME_DIR" \
            READING_ARM_LOG_DIR="$LOG_DIR" \
            READING_ARM_STOP_WAIT_SEC=0.2 \
            "$STOP_SCRIPT" >/dev/null 2>&1 || true
    fi
    rm -rf "$TMP_ROOT"
}
trap cleanup EXIT

touch "$FAKE_ROS_SETUP" "$FAKE_WORKSPACE_SETUP"

common_env=(
    READING_ARM_RUNTIME_DIR="$RUNTIME_DIR"
    READING_ARM_LOG_DIR="$LOG_DIR"
    READING_ARM_ROS_SETUP="$FAKE_ROS_SETUP"
    READING_ARM_WORKSPACE_SETUP="$FAKE_WORKSPACE_SETUP"
    READING_ARM_DRIVER_CMD="sleep 60"
    READING_ARM_SERVO_CMD="sleep 60"
    READING_ARM_AGENT_CMD="sleep 60"
    READING_ARM_SKIP_HEALTHCHECK=1
    READING_ARM_PROCESS_WAIT_SEC=0.1
    READING_ARM_STOP_WAIT_SEC=0.2
)

env "${common_env[@]}" "$START_SCRIPT"

for name in roarm_driver servo_controller arm_agent; do
    test -s "$RUNTIME_DIR/$name.pid"
    pid="$(cat "$RUNTIME_DIR/$name.pid")"
    kill -0 "$pid"
done

driver_pid="$(cat "$RUNTIME_DIR/roarm_driver.pid")"
servo_pid="$(cat "$RUNTIME_DIR/servo_controller.pid")"
agent_pid="$(cat "$RUNTIME_DIR/arm_agent.pid")"

env "${common_env[@]}" "$START_SCRIPT"
test "$(cat "$RUNTIME_DIR/roarm_driver.pid")" = "$driver_pid"
test "$(cat "$RUNTIME_DIR/servo_controller.pid")" = "$servo_pid"
test "$(cat "$RUNTIME_DIR/arm_agent.pid")" = "$agent_pid"

env "${common_env[@]}" "$STOP_SCRIPT"
for name in roarm_driver servo_controller arm_agent; do
    test ! -e "$RUNTIME_DIR/$name.pid"
done

failure_env=(
    "${common_env[@]}"
    READING_ARM_DRIVER_CMD="echo \$\$ > '$TMP_ROOT/failed-driver.pid'; exec sleep 60"
    READING_ARM_SERVO_CMD="exit 23"
)
if env "${failure_env[@]}" "$START_SCRIPT"; then
    echo "expected startup failure" >&2
    exit 1
fi

test ! -e "$RUNTIME_DIR/roarm_driver.pid"
test ! -e "$RUNTIME_DIR/servo_controller.pid"
test ! -e "$RUNTIME_DIR/arm_agent.pid"
failed_driver_pid="$(cat "$TMP_ROOT/failed-driver.pid")"
if kill -0 "$failed_driver_pid" 2>/dev/null; then
    echo "rollback left the driver process running" >&2
    exit 1
fi

echo "test_reading_arm_scripts PASS"
