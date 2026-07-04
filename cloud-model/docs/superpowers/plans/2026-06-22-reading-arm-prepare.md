# Reading Arm Prepare Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entering reading mode must return the reading arm to a known initial pose before starting book scan/tracking.

**Architecture:** Keep motion ownership in ROS. `servo_controller` owns joint targets and exposes a prepare signal; `arm_agent` exposes HTTP `/reading/prepare`; cloud-model calls prepare before `/reading/start` through `ArmAgentAdapter`.

**Tech Stack:** Python ROS2 nodes, HTTP `urllib`, cloud-model runtime scheduler, existing shell startup scripts.

---

### Task 1: ROS Prepare/Home State

**Files:**
- Modify: `~/ros2/src/face_track/face_track/arm_agent_core.py`
- Modify: `~/ros2/src/face_track/face_track/test_arm_agent_core.py`
- Modify: `~/ros2/src/face_track/face_track/servo_controller.py`
- Modify: `~/ros2/src/face_track/face_track/arm_agent.py`

- [x] Add failing tests for an initial pose helper that moves joints toward target and reports completion.
- [x] Run `python3 src/face_track/face_track/test_arm_agent_core.py` and confirm the new tests fail.
- [x] Implement the helper and wire `servo_controller` to a prepare topic.
- [x] Add `/reading/prepare` in `arm_agent` to publish the prepare request.
- [x] Re-run the ROS unit test.

### Task 2: Cloud-Model Prepare Before Start

**Files:**
- Modify: `~/cloud-model-safety-mainline/arm/agent_client.py`
- Modify: `~/cloud-model-safety-mainline/runtime_scheduler/adapters/arm.py`
- Modify: `~/cloud-model-safety-mainline/runtime_scheduler/coordinator.py`
- Test: `~/cloud-model-safety-mainline/runtime_scheduler/test_reading_prepare.py`

- [x] Add failing test that `RuntimeCoordinator.start_reading()` calls `prepare_reading()` before `start_reading()`.
- [x] Implement `agent_client.prepare_reading()` and adapter method.
- [x] Re-run focused test and py_compile.

### Task 3: Docs And Verification

**Files:**
- Modify: closest existing cloud-model runbook or add a small note under `docs/superpowers/plans`.

- [x] Document reading mode sequence and failure behavior.
- [ ] Verify both repos status and list remaining pre-existing untracked model files separately.


## Implementation Notes

The reading entry sequence is now:

1. `RuntimeCoordinator.start_reading()` acquires reading resources and pauses safety if configured.
2. `ArmAgentAdapter.prepare_reading()` calls `POST /reading/prepare?timeout=8`.
3. ROS `arm_agent` publishes `/book_prepare=True` and waits until `servo_controller` reports `/book_prepare_status=COMPLETE` and joint motion has settled.
4. Only after prepare succeeds does cloud-model call `POST /reading/start`.
5. If prepare fails or times out, cloud-model restores normal scheduler leases, resumes safety, and does not enter reading tracking.

The ROS prepare target is configured in `~/ros2/src/face_track/config/servo_params.yaml` with `initial_j1..initial_j4` and `prepare_tolerance`.


## Startup Guard Update

`./scripts/start_system.sh --with-arm` now checks that the running `arm_agent` exposes the new prepare-capable status fields (`preparing`, `prepare_complete`) after calling `~/ros2/start_reading_arm.sh`. This catches the common stale-process case where the arm stack was already running from an older build and the start script reused it. The check reads `/book/status` only; it does not move the arm during startup. Actual movement still happens only when reading mode calls `/reading/prepare`.


## Platform Camera Bandwidth Guard

Board testing showed that `/dev/video21` returns `VIDIOC_STREAMON: No space left on device` while the platform Orbbec camera is publishing. The reading arm camera recovers immediately after `scripts/stop_platform_camera.sh` stops the platform camera, without restarting `arm_agent`. Runtime scheduling therefore stops the platform camera before arm health/preparation and restarts it when reading stops or reading startup fails. This is controlled by `SCHEDULER_READING_STOPS_PLATFORM_CAMERA=1` by default.
