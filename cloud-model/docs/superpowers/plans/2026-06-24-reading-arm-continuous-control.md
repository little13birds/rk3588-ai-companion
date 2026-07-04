# Reading Arm Continuous Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** While the reading-arm state machine is active, publish arm joint commands at a fixed cadence instead of only publishing a few commands when visual frames arrive.

**Architecture:** Keep visual detection as an observation input and move command publishing to a fixed ROS timer inside `servo_controller`. `/face_info` updates the latest observation and target joint pose; the timer continuously republishes the current target pose during active states, including `ready`, so the arm is held under gravity even when visual frames are intermittent.

**Tech Stack:** Python ROS2 nodes in `~/ros2/src/face_track`, `sensor_msgs/JointState`, existing pure-logic tests under `face_track`, board-side `colcon build`.

---

## Current Baseline

- Baseline commit before this work: `~/ros2` commit `b03910b feat: stabilize reading arm control workflow`.
- `servo_controller` currently publishes `/joint_states` directly from state transitions, visual callbacks, prepare/search ticks, and manual jog callbacks.
- `roarm_driver` now ignores non-arm `/joint_states`, so chassis wheel joint messages no longer crash the arm driver.
- The observed field issue is not primarily large per-frame deltas. It is intermittent command bursts combined with gravity, mechanical compliance, and servo holding behavior.

## Target Behavior

- `idle`: do not continuously publish automatic arm commands.
- `preparing`, `startup_search`, `coarse_align`, `fine_align`, `ready`, `next_page_fine_align`, `next_page_local_search`, and `exit_return_home`: publish the latest arm target at a fixed frequency.
- `ready`: continue publishing the held target pose. This is required because the arm can sag or oscillate under gravity if command frames stop.
- `/face_info` should update state and target pose, not be the only timing source for command output.
- Short visual frame gaps should not interrupt command publishing or immediately trigger search.

## Parameters

Add to `~/ros2/src/face_track/config/servo_params.yaml`:

```yaml
control_publish_hz: 20.0
hold_publish_when_active: true
vision_stale_hold_sec: 0.3
vision_lost_sec: 0.8
```

Semantics:

- `control_publish_hz`: fixed command publish timer frequency.
- `hold_publish_when_active`: if true, active states keep publishing the last target pose even without new visual frames.
- `vision_stale_hold_sec`: visual observations older than this are not used for new corrections, but the current target is still held.
- `vision_lost_sec`: visual observations older than this may trigger the existing lost-book/search recovery.

## Implementation Tasks

### Task 1: Extract Active-State Policy

**Files:**
- Modify: `~/ros2/src/face_track/face_track/servo_controller.py`
- Modify: `~/ros2/src/face_track/face_track/test_servo_state_machine.py`

- [ ] Add a pure helper:

```python
def should_publish_hold_command(state, preparing, tracking, hold_enabled=True):
    if preparing:
        return bool(hold_enabled)
    if not hold_enabled:
        return False
    return state in (
        STATE_STARTUP_SEARCH,
        STATE_COARSE_ALIGN,
        STATE_FINE_ALIGN,
        STATE_READY,
        STATE_NEXT_PAGE_FINE_ALIGN,
        STATE_NEXT_PAGE_LOCAL_SEARCH,
        STATE_EXIT_RETURN_HOME,
    )
```

- [ ] Add tests that `STATE_READY` publishes, `STATE_IDLE` does not publish, and `preparing=True` publishes.

### Task 2: Add Fixed Command Publish Timer

**Files:**
- Modify: `~/ros2/src/face_track/face_track/servo_controller.py`
- Modify: `~/ros2/src/face_track/config/servo_params.yaml`
- Modify: `~/ros2/src/face_track/face_track/test_servo_state_machine.py`

- [ ] Declare `control_publish_hz` and `hold_publish_when_active`.
- [ ] Add `self.last_published_joints = None` to support future diagnostics.
- [ ] Add a timer:

```python
self.control_timer = self.create_timer(
    1.0 / float(self.get_parameter('control_publish_hz').value),
    self.control_tick,
)
```

- [ ] Implement:

```python
def control_tick(self):
    if not should_publish_hold_command(
        self.state,
        self.preparing,
        self.tracking,
        self.get_parameter('hold_publish_when_active').value,
    ):
        return
    self._publish_joint_state()
```

- [ ] Keep existing immediate publishes in place for the first version. This intentionally minimizes behavior risk; the timer adds continuous hold frames without removing existing state updates.

### Task 3: Add Visual Freshness Gate

**Files:**
- Modify: `~/ros2/src/face_track/face_track/servo_controller.py`
- Modify: `~/ros2/src/face_track/face_track/test_servo_state_machine.py`
- Modify: `~/ros2/src/face_track/config/servo_params.yaml`

- [ ] Add a pure helper:

```python
def classify_visual_freshness(age_sec, stale_hold_sec, lost_sec):
    if age_sec <= stale_hold_sec:
        return "fresh"
    if age_sec <= lost_sec:
        return "hold"
    return "lost"
```

- [ ] Add tests for `fresh`, `hold`, and `lost` thresholds.
- [ ] Track `self.last_face_info_time` when `/face_info` arrives.
- [ ] In the no-book branch, use `vision_lost_sec` as the loss threshold before entering search. During the hold window, keep publishing current target and do not update corrections from stale data.

### Task 4: Verification

**Files:**
- Test: `~/ros2/src/face_track/face_track/test_servo_state_machine.py`
- Test: `~/ros2/src/face_track/face_track/test_arm_agent_core.py`
- Test: `~/ros2/src/face_track/face_track/test_arm_agent_debug.py`

- [ ] Run:

```bash
cd ~/ros2
export PYTHONPATH=/opt/ros/humble/local/lib/python3.10/dist-packages:/opt/ros/humble/lib/python3.10/site-packages:/home/elf/ros2/src/face_track
export LD_LIBRARY_PATH=/opt/ros/humble/lib
python3 src/face_track/face_track/test_servo_state_machine.py
python3 src/face_track/face_track/test_arm_agent_core.py
python3 src/face_track/face_track/test_arm_agent_debug.py
```

- [ ] Build:

```bash
cd ~/ros2
source /opt/ros/humble/setup.bash
source ~/ros2/install/setup.bash
colcon build --packages-select face_track --symlink-install
```

- [ ] Restart reading arm services:

```bash
~/ros2/stop_reading_arm.sh
~/ros2/start_reading_arm.sh
```

- [ ] Verify `/frame.jpg?debug=1&w=480` returns `200` and `/book/status` remains reachable.

## Explicit Non-Goals

- Do not tune gravity compensation in this change.
- Do not change `roarm_driver` `spd` or `acc` in this change.
- Do not rename `/joint_states` to `/arm_joint_states` in this change.
- Do not change the camera topology or platform-camera scheduler behavior in this change.
