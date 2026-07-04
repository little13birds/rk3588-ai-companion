# Resource Scheduler Branch Modification Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` for parallel review/prototyping, or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This historical plan was scoped to `feat/safety-guard-mainline` before the 2026-06-27 merge into `master` and intentionally excludes ESP32/watch-camera work.

**Date:** 2026-06-21  
**Historical target branch:** `feat/safety-guard-mainline`（已于 2026-06-27 合入 `master`）  
**Goal:** Add an in-process resource scheduler to `cloud-model` so reading mode, safety guard, dashboard snapshots, TTS, future person-seek/follow, and ROS-side services do not silently fight over USB bandwidth, NPU, CPU, speaker, microphone, or robot arm resources.

## Inputs Reviewed

- `CLAUDE.md`: current `cloud-model` architecture, startup, branch boundary, safety/dashboard defaults.
- `docs/superpowers/plans/2026-06-13-reading-arm-background-scripts.md`: ROS2 reading-arm lifecycle via `~/ros2/start_reading_arm.sh` and `stop_reading_arm.sh`.
- `docs/superpowers/plans/2026-06-13-reading-photo-latency.md`: arm-side full frame vs VLM resized image split.
- `docs/superpowers/plans/2026-06-20-safety-guard-integration.md`: safety guard integration and RKNN/ROS architecture.
- Previous safety review doc: safety monitor pause/resume need, speaker arbitration, stale camera, NPU/CPU concerns.
- `rk3588_full_safety/ROS_DEPTH_CAMERA_NOTES.md`: ROS RGB/depth topic behavior and multiple subscriber observations.
- `rk3588_full_safety/DEPTH_FUSION_PLAN.md`: future depth fusion workload and tri-state safety implications.
- `face_identity_rk3588/PERSON_TRACKING_DESIGN.md`: future person tracker combines YOLOv8-pose + face recognition on RKNN and needs runtime budget.
- Actual board observations: `/dev/video21` V4L2 may fail with `VIDIOC_STREAMON ... No space left on device`; ROS `/camera/color/image_raw` can be shared as a topic, but NPU/CPU and USB topology still require scheduling.

## Known Resource Conflicts

### USB and Camera

- `/dev/video21` is exclusive and sensitive to USB topology. It can open yet fail to stream with `No space left on device`.
- Orbbec/Astra driver owns hardware and publishes `/camera/color/image_raw` and `/camera/depth/image_raw`; ROS fan-out supports multiple subscribers, but each downstream consumer still spends CPU and memory bandwidth.
- Reading arm `arm_agent`, safety guard, dashboard snapshot, obstacle monitor, depth fusion, and future person-seek can all consume camera frames.
- Starting another direct V4L2 reader while Orbbec/USB audio/arm serial share the same USB2 branch can break `arm_agent` frame capture.

### NPU/RKNN

- Safety guard uses pose + hand + hazard RKNN models.
- Reading arm uses book detection RKNN in `arm_agent`.
- Future face/person tracker uses YOLOv8-pose + face detector + face recognizer RKNN.
- Future monocular depth fusion may add another model.
- Each module currently owns its own runtime/lock; there is no global budget, priority, or mode-level exclusion.

### CPU and Encoding

- Safety native path has historically encoded annotated JPEG every tick; even after optimization work, scheduler should still treat debug JPEG and VLM frame preparation as CPU tasks.
- Dashboard streams/snapshots, VLM image resize, arm frame serving, and safety event recording all add JPEG encode/decode pressure.
- ASR/AEC/TTS main loop must remain responsive.

### Audio and Speech

- Normal assistant, reading mode, dashboard speech, and safety alerts all share `RealtimeSpeaker`.
- Safety high-risk alerts may need to interrupt; medium/low alerts should not always destroy reading/story state.
- AEC and KWS behavior depends on clean playback/mic state transitions.

### Robot Arm

- `roarm_driver` owns `/dev/roarm`.
- `servo_controller` and `arm_agent` are started/stopped through `~/ros2/start_reading_arm.sh` and `stop_reading_arm.sh`.
- `cloud-model` currently only calls `arm_agent` HTTP endpoints; it does not own ROS process lifecycle.

### ROS Process Lifecycle

- ROS-side services are split across `~/ros2`, `~/cloud-model-safety-mainline`, `rk3588_full_safety`, and `face_identity_rk3588`.
- Current branch should not absorb ESP32 code and should avoid rewriting ROS launch systems in the first pass.
- The scheduler should first wrap known scripts/endpoints rather than replacing them.

## Design Direction

Add a new package:

```text
cloud-model/
  runtime_scheduler/
    __init__.py
    resources.py
    leases.py
    scheduler.py
    modes.py
    state.py
    probes.py
    adapters/
      __init__.py
      arm.py
      safety.py
      speech.py
      ros_process.py
```

Principle:

```text
cloud-model = policy and mode owner
~/ros2 = execution layer for ROS processes
safety_guard/face/person/depth modules = schedulable clients
```

Do not put the scheduler under `safety_guard/`, because it must also manage reading, TTS, dashboard, and future person-seek. Do not put it under `dashboard/`, because dashboard is only a view/API layer. Do not put the first version under `~/ros2`, because `cloud-model` currently owns the conversational mode state.

## Resource Model

Initial resources:

```text
USB_V4L2_CAMERA        exclusive, direct /dev/video21
ROS_RGB_CAMERA         shared, but budgeted by mode
ROS_DEPTH_CAMERA       shared, but budgeted by mode
NPU_CORE_0             physical RK3588 NPU core 0
NPU_CORE_1             physical RK3588 NPU core 1
NPU_CORE_2             physical RK3588 NPU core 2
NPU_SAFETY             schedulable model budget
NPU_BOOK               schedulable model budget
NPU_PERSON_FACE        future schedulable model budget
CPU_VISION             soft budget
SPEAKER_TTS            exclusive, priority/preemptable
MIC_ASR_KWS            protected, always-on unless explicit maintenance
ROARM_SERIAL           exclusive /dev/roarm through roarm_driver
ARM_AGENT_HTTP         external service health and tracking state
```

Implementation note 2026-06-23:

- `NPU_CORE_0/1/2` are now represented as first-class scheduler resources.
- `NORMAL_POLICY` reserves core0/core1 for safety-oriented workloads.
- `READING_POLICY` reserves core2 for book/reading workloads.
- Legacy logical resources `NPU_SAFETY/NPU_BOOK/NPU_PERSON_FACE` remain for compatibility and older tests.
- This is scheduler-level expression only. Actual RKNN C++ model loading still needs explicit core-mask binding in a later native runtime change.

Resource lease fields:

```text
resource
owner
mode
priority
exclusive/shared
expires_at / heartbeat
preemptible
reason
metadata
```

Mode priorities:

```text
safety_alert    highest
emergency_stop  highest
reading         high but preemptible by safety_alert
person_seek     high, mutually exclusive with reading in v1
normal          baseline
dashboard       background
maintenance     explicit/manual only
```

## Mode Policies

### `normal`

- Safety guard enabled at configured default.
- ASR/KWS/AEC/TTS normal.
- Dashboard enabled.
- Reading arm process can be down or idle.
- Dashboard snapshots can query `arm_agent` only if healthy; otherwise return a structured unavailable state.

### `reading`

Acquire:

```text
ROARM_SERIAL
ARM_AGENT_HTTP
NPU_BOOK
ROS_RGB_CAMERA or USB_V4L2_CAMERA depending on arm_agent source
SPEAKER_TTS
```

Actions:

- Ensure reading arm service is healthy. In v1, call existing `~/ros2/start_reading_arm.sh` only if configured to auto-start; otherwise report unhealthy.
- Call `agent_client.start_reading()`.
- Reduce or pause safety monitor:
  - v1 safe default: `safety_guard.pause("reading")`.
  - fallback if pause not implemented yet: `set_target_frequency_hz(1.0)` and suppress hazard pass.
- Block person-seek/follow and depth fusion heavy modes.
- On exit, call `agent_client.stop_reading()` and resume safety.

### `safety_alert`

Acquire:

```text
SPEAKER_TTS with preempt
NPU_SAFETY already owned by safety guard
```

Actions:

- High/critical alerts may interrupt current TTS.
- Medium alerts only speak when speaker is idle or when configured.
- Safety alert must not permanently corrupt reading/story mode; after alert, scheduler restores the previous mode if allowed.

### `person_seek` / `person_follow` Future Mode

Acquire:

```text
ROS_RGB_CAMERA
ROS_DEPTH_CAMERA
NPU_PERSON_FACE
CPU_VISION
```

Actions:

- Mutually exclusive with reading in v1.
- Shares obstacle guard/depth topics but must not disable safety stop logic.
- Motion controller owns depth/stop-distance final decisions.

### `maintenance`

- Used for camera/USB probing and NPU benchmarking.
- Requires explicit operator request.
- May stop safety/reading/person services.

## Phase Plan

**Implementation status (2026-06-21):** Phase 0-4 v1 has been implemented in
`runtime_scheduler/` and wired into `main.py`, `safety_guard`, `arm/agent_client`,
and dashboard read-only APIs. Phase 5+ remains planned work and is intentionally
not included in the first commit.

**Progress update (2026-06-23):** The dashboard/control/sleep/NPU follow-up has been implemented in
`cloud-model-safety-mainline` only. No `~/ros2` files were modified. The parent dashboard now has a
dedicated control page, `/api/system/features`, a safe chassis adapter that can publish `/cmd_vel_raw`
only when enabled, SQLite-backed sleep settings, child-presence based sleep state, and physical NPU
core scheduler resources. See `docs/superpowers/plans/2026-06-23-dashboard-control-sleep-npu-progress.md`.

### Phase 0: Documentation and Inventory

**Files:**
- Create: `docs/superpowers/plans/2026-06-21-resource-scheduler-branch-plan.md`
- Modify: `CLAUDE.md`

- [x] Add this plan to branch docs.
- [x] Add a `Resource Scheduling` section in `CLAUDE.md` with the package path, constraints, and non-ESP32 boundary.
- [x] Document currently known conflicts and command-level workarounds.
- [x] Keep `~/ros2` scripts as execution layer; do not modify ROS scripts in this phase.

### Phase 1: Scheduler Core, Dry-Run Only

**Files:**
- Create: `runtime_scheduler/__init__.py`
- Create: `runtime_scheduler/resources.py`
- Create: `runtime_scheduler/leases.py`
- Create: `runtime_scheduler/scheduler.py`
- Create: `runtime_scheduler/modes.py`
- Create: `runtime_scheduler/state.py`
- Create: `runtime_scheduler/test_scheduler.py`

- [x] Implement `Resource` enum and resource capabilities.
- [x] Implement `ResourceLease` and resource snapshot dictionaries.
- [x] Implement `ResourceScheduler.acquire_many()`, `release_owner()`, `release_mode()`, and snapshot helpers.
- [x] Implement conflict results without process side effects.
- [x] Add unit tests for exclusive resources, priority preemption, TTL expiration, and shared resource limits.
- [x] No runtime behavior changes in scheduler core itself.

### Phase 2: Health Probes and Dashboard Visibility

**Files:**
- Optional later: `runtime_scheduler/probes.py`
- Modify: `dashboard/state.py`
- Modify: `dashboard/server.py`
- Create/modify dashboard tests.

- [x] Add probes for:
  - `arm_agent` `/book/status` and `/frame.jpg`.
  - safety guard status through adapter.
- [ ] Add later probes for:
  - ROS RGB topic freshness if ROS is available.
  - process presence for `roarm_driver`, `servo_controller`, `arm_agent` via PID files.
- [x] Add read-only state:
  - `/api/system/mode`
  - `/api/system/resources`
  - `/api/system/conflicts`
- [x] Dashboard must not start/stop processes in this phase.
- [x] Tests use fake providers and do not require ROS.

### Phase 3: Safety Pause/Resume Adapter

**Files:**
- Modify: `safety_guard/service.py`
- Modify: `safety_guard/monitor.py`
- Modify: `safety_guard/ros_camera.py`
- Create: `runtime_scheduler/adapters/safety.py`
- Add tests or lightweight smoke scripts.

- [x] Add `SafetyGuardService.pause(reason)` and `resume(reason)`.
- [x] Add `status()` containing enabled/paused/reason/target_hz/camera stats/queue depth.
- [x] Monitor must stop running RKNN while paused.
- [x] Decide whether camera subscription remains active in pause v1:
  - safer first implementation: keep ROS subscription but skip RKNN.
  - stronger later implementation: stop/recreate `RosRgbCamera` on pause/resume.
- [ ] Add stale-frame guard if not already present.
- [x] Verify with unit sample that paused monitor skips runtime processing and resumed monitor processes again.
- [x] Coordinator test verifies entering reading pauses safety and leaving reading restores it.

### Phase 4: Reading Mode Adapter

**Files:**
- Create: `runtime_scheduler/adapters/arm.py`
- Optional later: `runtime_scheduler/adapters/ros_process.py`
- Modify: `arm/agent_client.py`
- Modify: `main.py`
- Add tests with fake HTTP/script runners.

- [x] Add `agent_client.health()` that checks `/book/status` and optionally `/frame.jpg`.
- [x] Add `ArmAgentAdapter.ensure_running()` that can call `~/ros2/start_reading_arm.sh` only when `SCHEDULER_AUTO_START_READING_ARM=1`.
- [x] Default is conservative: if the arm service is not healthy and auto-start is off, return a clear error and let cloud-model continue without crashing.
- [x] Replace direct `agent_client.start_reading()` in `main.py` with `RuntimeCoordinator.start_reading()`.
- [x] Replace direct `agent_client.stop_reading()` with `RuntimeCoordinator.stop_reading()`.
- [x] Preserve old arm_agent direct path when scheduler is disabled via `RESOURCE_SCHEDULER_ENABLED=0`.

### Phase 5: Speech Arbitration

**Files:**
- Create: `runtime_scheduler/adapters/speech.py`
- Modify: `safety_guard/announcer.py`
- Modify: `main.py`
- Add tests around high/medium safety alerts.

- [ ] Introduce a minimal `SpeechArbiter` around `RealtimeSpeaker`.
- [ ] Classify speech requests:
  - `normal_response`
  - `reading`
  - `dashboard_message`
  - `safety_medium`
  - `safety_high`
  - `interrupt_reply`
- [ ] High/critical safety may preempt.
- [ ] Medium safety queues or skips if reading/story is active.
- [ ] After safety alert, mode restoration must be explicit and logged.

### Phase 6: NPU Budgeting

**Files:**
- Modify: `runtime_scheduler/resources.py`
- Modify: `runtime_scheduler/scheduler.py`
- Modify: safety adapter and future face/person adapter hooks.

- [ ] Start with coarse-grained NPU ownership by mode.
- [ ] In `reading`, book detection has priority and safety is paused or reduced.
- [ ] In `normal`, safety guard owns `NPU_SAFETY`.
- [ ] In future `person_seek`, scheduler refuses reading and safety-heavy debug modes.
- [ ] Add status counters for denied/acquired/preempted NPU leases.

### Phase 7: Optional ROS/System Integration

**Files:**
- Optional only after v1 is stable.

- [ ] Consider moving process lifecycle to systemd user services or ROS launch only after the in-process scheduler is proven.
- [ ] Keep `start_reading_arm.sh` and `stop_reading_arm.sh` as compatibility wrappers.
- [ ] Do not add ESP32/watch-camera demo to this branch.

## Proposed File Changes Summary

Create:

```text
runtime_scheduler/
runtime_scheduler/adapters/
docs/superpowers/plans/2026-06-21-resource-scheduler-branch-plan.md
```

Modify:

```text
CLAUDE.md
main.py
arm/agent_client.py
safety_guard/service.py
safety_guard/monitor.py
safety_guard/ros_camera.py
safety_guard/announcer.py
dashboard/state.py
dashboard/server.py
```

Do not modify in this branch unless explicitly requested:

```text
vision/watch_camera.py
ESP32 watch-camera scripts
face_identity_rk3588 service code
rk3588_full_safety standalone service
~/ros2 source code, except documented script wrappers if specifically approved
```

## Environment Flags

```bash
RESOURCE_SCHEDULER_ENABLED=1
SCHEDULER_AUTO_START_READING_ARM=0
SCHEDULER_LOG_PERIOD_SEC=5
SCHEDULER_READING_PAUSES_SAFETY=1
SCHEDULER_READING_SAFETY_HZ=1.0
SCHEDULER_REQUIRE_FRAME_HEALTH=1
```

Default posture:

- Scheduler enabled after tested.
- Auto-start reading arm disabled until repeated board tests pass.
- Safety paused during reading by default once pause/resume is implemented.

## Verification Matrix

### Unit

- [ ] Scheduler resource conflict tests pass.
- [ ] Mode transition tests pass.
- [ ] Fake arm adapter tests pass.
- [ ] Dashboard system status tests pass.

Commands:

```bash
cd ~/cloud-model-safety-mainline
python3 -m runtime_scheduler.test_scheduler
python3 -m runtime_scheduler.test_coordinator
python3 -m arm.test_agent_client
python3 -m dashboard.test_state
python3 -m dashboard.test_server_system
python3 -m safety_guard.test_monitor_pause
python3 -m py_compile main.py arm/agent_client.py dashboard/state.py dashboard/server.py safety_guard/__init__.py safety_guard/service.py safety_guard/monitor.py runtime_scheduler/*.py runtime_scheduler/adapters/*.py
```

### Board Smoke

- [ ] Start cloud-model with scheduler disabled; behavior matches current branch.
- [ ] Start cloud-model with scheduler enabled; normal mode starts safety/dashboard.
- [ ] Enter reading mode with arm service healthy; safety pauses/reduces and reading works.
- [ ] Exit reading; safety resumes.
- [ ] Enter reading with arm service unhealthy; cloud-model reports structured failure and does not crash.
- [ ] Trigger high safety alert during normal speech; alert preempts cleanly.
- [ ] Trigger medium safety alert during reading; behavior follows policy.

### Resource Tests

- [ ] With `/dev/video21` failing, scheduler status reports V4L2 unavailable and suggests ROS RGB fallback.
- [ ] With ROS RGB active, multiple subscribers do not block but CPU/NPU budgets are visible.
- [ ] With safety paused, RKNN safety calls stop.
- [ ] With dashboard snapshot request, no direct V4L2 open occurs.

## Rollback

- Set `RESOURCE_SCHEDULER_ENABLED=0` to preserve current branch behavior.
- Keep direct `agent_client.start_reading()` code path behind the disabled scheduler flag until the scheduler has passed board smoke tests.
- Do not delete existing `start_reading_arm.sh` / `stop_reading_arm.sh`.
- Do not remove safety guard env flags; scheduler should call existing public methods.

## Acceptance Criteria

- There is a single visible system mode and resource snapshot from dashboard/API.
- Reading mode no longer silently competes with safety RKNN at full rate.
- Safety high alerts can still interrupt when configured.
- Missing/unhealthy `arm_agent` produces an explicit failure instead of a long tool timeout.
- The branch remains ESP32-free.
- All new behavior can be disabled through `RESOURCE_SCHEDULER_ENABLED=0`.
