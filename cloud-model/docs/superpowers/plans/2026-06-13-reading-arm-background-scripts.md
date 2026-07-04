# Reading Arm Background Scripts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one background start script and one stop script for the reading-mode arm ROS2 stack.

**Architecture:** The start script sources ROS2, launches each component in its own process group, records the group leader PID, and rolls back partial startup failures. The stop script terminates the groups in reverse dependency order. Both scripts serialize operations with `flock`; logs are timestamped under `~/ros2/log/reading_arm/`.

**Tech Stack:** Bash 5, ROS2 Humble, `setsid`, `flock`, `curl`

---

### Task 1: Lifecycle Regression Test

**Files:**
- Create: `test_reading_arm_scripts.sh`

- [ ] Write a shell test that launches fake `sleep` components.
- [ ] Verify repeated start preserves the same PIDs.
- [ ] Verify stop removes all PID files and processes.
- [ ] Verify a failed middle component rolls back the first component.

### Task 2: Start Script

**Files:**
- Create: `start_reading_arm.sh`

- [ ] Source `/opt/ros/humble/setup.bash` and `~/ros2/install/setup.bash`.
- [ ] Launch `roarm_driver`, `servo_controller`, and `arm_agent` in order.
- [ ] Store process-group PIDs in `/tmp/reading_arm`.
- [ ] Store timestamped logs and `*.log` latest symlinks under `~/ros2/log/reading_arm`.
- [ ] Poll `/book/status`; roll back all started groups if startup fails.

### Task 3: Stop Script

**Files:**
- Create: `stop_reading_arm.sh`

- [ ] Stop `arm_agent`, `servo_controller`, and `roarm_driver` in reverse order.
- [ ] Escalate from `SIGTERM` to `SIGKILL` after a bounded wait.
- [ ] Remove stale PID files and leave unrelated processes untouched.

### Task 4: Board Verification

- [ ] Run `bash -n` and the fake-process regression test.
- [ ] Run the real start script and verify three ROS2 processes plus HTTP status.
- [ ] Run the stop script and verify all three process groups are gone.
- [ ] Sync the board-source scripts back to Windows.
