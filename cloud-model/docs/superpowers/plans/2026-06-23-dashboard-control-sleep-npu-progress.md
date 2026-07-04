# Dashboard Control, Sleep Rules, and NPU Scheduler Progress

**Date:** 2026-06-23  
**Historical branch:** `feat/safety-guard-mainline`（已于 2026-06-27 合入 `master`）  
**Scope:** `~/cloud-model-safety-mainline` only. `~/ros2` was not modified.

## Summary

This update implements the local todo items for parent dashboard control, sleep-state rules, dashboard feature visibility, and RK3588 NPU scheduler representation.

It intentionally keeps hardware side effects conservative:

- Chassis movement is disabled by default and returns a reserved/safe status.
- When explicitly enabled, dashboard movement publishes intent to `/cmd_vel_raw`, not `/cmd_vel`.
- Existing obstacle guard remains the final owner of `/cmd_vel`.
- ROS package source under `~/ros2` is untouched.

## Implemented

### Startup And Camera Scheduling

`scripts/start_system.sh` is the normal entry point for the integrated system.
It now treats the platform camera and reading-arm camera as different resources:

- On system startup, `scripts/start_platform_camera.sh` starts the platform
  Orbbec RGB/depth publisher for safety guard, dashboard preview, and obstacle
  guard.
- The platform camera default launch command is the same 640x480@30fps explicit
  profile used by the verified chassis/perception startup notes:
  `ros2 launch orbbec_camera orbbec_camera.launch.py camera_type:=astraproplus enable_ir:=false enable_color:=true enable_depth:=true color_width:=640 color_height:=480 color_fps:=30 depth_width:=640 depth_height:=480 depth_fps:=30`.
  Do not make the platform camera default 20fps unless the driver profile is
  verified again; the current board rejected 20fps depth with
  `No matched video stream profile`.
- Reading-arm services are not started at boot unless `--with-arm` is supplied.
  Instead, `SCHEDULER_AUTO_START_READING_ARM=1` is now the `start_system.sh`
  default, so entering reading mode can start `~/ros2/start_reading_arm.sh`
  on demand.
- Entering reading mode follows this order:
  pause safety inference -> stop platform camera -> ensure/start arm_agent ->
  `/reading/prepare` -> `/reading/start`.
- Exiting reading mode or failing to start reading restores normal mode:
  release reading resources -> restart platform camera -> resume safety guard.
- `scripts/stop_platform_camera.sh` has a fallback for the case where platform
  camera topics already had publishers but no PID file was recorded; it can
  stop matching Orbbec camera launch/node processes so reading mode can release
  USB bandwidth.

Rollback controls:

```bash
./scripts/start_system.sh --no-auto-start-arm
SCHEDULER_AUTO_START_READING_ARM=0 ./scripts/start_system.sh
PLATFORM_CAMERA_LAUNCH_CMD='ros2 launch orbbec_camera astra.launch.xml' ./scripts/start_system.sh
```

### Parent Control Page

The homepage is now a status preview. Movement controls moved to a dedicated dashboard page:

```text
首页: camera/status/env/conversation
记录: reading records/history/timeline
看护: safety + sleep management
控制: D-pad/find-child/emergency-stop/feature status
```

Files:

```text
dashboard/parent-dashboard.html
dashboard/client_state.js
scripts/test_dashboard_control_page.py
```

### Chassis Control Adapter

New adapter:

```text
dashboard/chassis_control.py
```

Behavior:

- Default under `./scripts/start_system.sh`: enabled. Set
  `DASHBOARD_CHASSIS_CONTROL_ENABLED=0` to disable dashboard chassis publishing
  and skip startup of the support stack.
- Publishes `geometry_msgs/Twist` to `/cmd_vel_raw`.
- Default movement speeds mirror `teleop_twist_keyboard`: `linear.x=0.5`,
  `angular.z=1.0`; use environment overrides for slower supervised tests.
- Rate limits non-stop commands.
- Stop and emergency stop are never dropped by the rate limiter.
- The parent dashboard direction buttons publish immediately on press, repeat
  every 150 ms while held, and send `stop` on release/cancel/lost focus/page
  hide. This keeps commands alive for `depth_obstacle_guard`, whose
  `cmd_timeout_s` treats stale input as `cmd_stale`.
- Actual chassis motion still depends on the ROS chain
  `dashboard_chassis_control -> /cmd_vel_raw -> depth_obstacle_guard -> /cmd_vel -> driver_node`.
  If `/depth_camera/obstacle_status` reports `blocked`, obstacle guard may output
  zero velocity even though the dashboard is publishing correctly.
- `main.py` reuses `person_tasks`' ROS adapter to start the support stack
  (`Mcnamu_driver_X3`, `base_node_X3`, `fused_pose_monitor`, `obstacle_guard`)
  when dashboard chassis control is enabled.
- Manual directions are blocked while reading mode or a person task is active;
  `stop` and emergency stop remain available.

Environment:

```bash
DASHBOARD_CHASSIS_CONTROL_ENABLED=1
DASHBOARD_CHASSIS_CMD_VEL_RAW_TOPIC=/cmd_vel_raw
DASHBOARD_CHASSIS_LINEAR_MPS=0.5
DASHBOARD_CHASSIS_ANGULAR_RADPS=1.0
DASHBOARD_CHASSIS_MIN_INTERVAL_SEC=0.08
```

The dashboard "realtime socket" entry is only a reserved push channel for future
WebSocket/SSE state updates. Current manual chassis control uses HTTP
`/api/move` requests that repeat while a direction button is held; the
low-latency motion path is the ROS chain after `/cmd_vel_raw`.

### Dashboard APIs

Added or changed:

```text
GET  /api/system/features
GET  /api/sleep/children
POST /api/sleep/presence
POST /api/move
POST /api/move/find-child
POST /api/move/emergency-stop
```

`/api/system/features` reports which features are implemented, connected, reserved, or disabled. The control page renders this status so teammates can see whether movement/find-child/realtime socket/sleep rules are actually active.

### Sleep Rules

Sleep state is no longer time-only.

Inputs:

```text
bedtime
children: configured unique_name list
grace_minutes
aid_active
recent /api/sleep/presence updates
```

State rules:

```text
aid_active                       -> sleeping
now < bedtime                    -> awake
children configured but unseen   -> away
now < bedtime + grace_minutes    -> awake
otherwise                        -> restless
```

Sleep settings persist through the dashboard SQLite database:

```text
dashboard/event_store.py -> dashboard_settings key/value table
```

Sleep aid status is currently state/timer-only:

- `/api/sleep/aid/start` and `/api/sleep/aid/stop` update dashboard state and activity records.
- They do not play white noise, music, or bedtime-story audio yet.
- The frontend applies the API response immediately after start/stop/save/remind so UI state does
  not wait for the 30s care-page polling interval; active aid responses start a local countdown from
  `aid_remaining_sec`, and the aid box resets locally when the countdown reaches zero.
- The aid box uses "助眠计时中" wording instead of "正在播放" until real audio playback is connected.

### NPU Scheduler Resources

Scheduler resources now include:

```text
NPU_CORE_0
NPU_CORE_1
NPU_CORE_2
```

Policy mapping:

```text
normal  -> npu_core_0 + npu_core_1
reading -> npu_core_2
```

Compatibility:

- `NPU_SAFETY`, `NPU_BOOK`, and `NPU_PERSON_FACE` remain in the enum.
- Existing logical-resource tests still pass.
- This does not yet bind RKNN model execution to a specific physical NPU core. That requires a later native RKNN runtime/core-mask change.

### Book Matching Orientation

Book database matching now queries both directions for each rectified page:

```text
upright query
rot180 query
select best score with text
```

This reduces failures when a page/book is upside down.

File:

```text
book_match_client.py
test_book_match_client.py
```

### Control Page Camera and Existing Backend Hooks

The parent control page now reuses the same `/api/camera/snapshot` source as the home/care pages.
The browser targets a 67ms refresh interval, roughly 15 FPS, but keeps only one snapshot request
in flight per camera panel so slow network or slow JPEG capture will reduce real FPS instead of
queueing stale requests.

The control page also surfaces existing backend data instead of leaving blank placeholders:

```text
GET /api/system/features
GET /api/system/resources
GET /api/system/conflicts
GET /api/sleep/children
```

It shows chassis status/topic, resource lease/conflict counts, configured child names, and all
known feature flags. Reserved or disabled backend features remain visibly marked as reserved rather
than being presented as implemented.

Files:

```text
dashboard/parent-dashboard.html
dashboard/client_state.js
dashboard/test_client_state.js
dashboard/test_server_static.py
```

## Tests Run

```bash
node dashboard/test_client_state.js
python3 -m dashboard.test_server_static
python3 -m dashboard.test_event_store
python3 -m dashboard.test_chassis_control
python3 -m dashboard.test_state
python3 -m dashboard.test_server_system
python3 -m runtime_scheduler.test_scheduler
python3 -m runtime_scheduler.test_coordinator
python3 -m scripts.test_dashboard_control_page
python3 -m scripts.test_dashboard_frontend_timeline
python3 -m scripts.test_start_system_script
python3 -m test_book_match_client
python3 -m test_reading_mode
python3 -m py_compile dashboard/event_store.py dashboard/chassis_control.py dashboard/state.py dashboard/server.py main.py runtime_scheduler/resources.py runtime_scheduler/modes.py runtime_scheduler/scheduler.py book_match_client.py
git diff --check
```

## Important Boundaries

- Do not modify `~/ros2` from this branch while teammates are working there.
- Do not publish directly to `/cmd_vel` from the parent dashboard.
- Keep `DASHBOARD_CHASSIS_CONTROL_ENABLED=0` only when chassis hardware is disconnected or movement tests are not allowed.
  The normal startup now arms the safe `/cmd_vel_raw -> obstacle_guard -> /cmd_vel` path.
- NPU core scheduling is not the same as RKNN core binding; this update only prepares scheduler policy.

## Follow-Ups

- Add native RKNN core-mask support in the C++ model loader.
- Connect real person-seek/find-child backend to the control page.
- Connect face identity / child unique_name updates to `/api/sleep/presence`.
- Add WebSocket or server-sent events if dashboard polling becomes too slow.
- Add a real abnormal-sound detector before marking that feature enabled.
