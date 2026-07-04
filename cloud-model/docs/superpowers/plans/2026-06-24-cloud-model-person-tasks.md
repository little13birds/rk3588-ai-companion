# Cloud-Model Person Tasks Integration Notes

## Scope

This update adds a cloud-model-side control layer for person seek/follow tasks.
It does not change ROS perception or obstacle-avoidance algorithms.

## Implemented

- Added `person_tasks` helpers:
  - `roles.py`: maps voice-friendly roles to identity names.
    - `A` / `角色A` -> `tao`
    - `B` / `角色B` -> `xiao`
    - `我` / `nearest` -> nearest visible person
  - `intent.py`: deterministic fallback parser for high-risk motion phrases.
  - `tools.py`: OpenAI tool schemas and execution helper.
  - `ros_adapter.py`: starts/stops ROS person seek/follow tasks through fixed
    launch commands. It does not publish Twist directly.
  - `controller.py`: small facade used by tools and debug CLI.
    It monitors `person_seek` completion through `http://127.0.0.1:8092/status.json`
    and emits a `seek_arrived` event for cloud-model TTS when the state/reason
    becomes `ARRIVED` / `arrived`.
- Added two model tools:
  - `control_person_follow`
  - `observe_people_identity`
- Added deterministic main-loop fallback for motion commands:
  - `跟着我`, `跟我走`, `跟我来` -> follow nearest
  - `跟着角色A` -> follow `tao`
  - `找一下角色B在哪里` -> seek `xiao`
  - `不要跟了` -> stop active person seek/follow tasks
- Added `debug_runtime.py` commands:
  - `follow-me`
  - `follow-a`
  - `follow-b`
  - `seek-a`
  - `seek-b`
  - `stop-person`
  - `observe-people`
- `observe_people_identity` now uses the normal-mode platform camera snapshot
  injected from `SafetyGuardService.camera_snapshot()` before falling back to
  the reading-arm `arm_agent` capture path. This keeps "我是谁/你认识我吗"
  on the same camera view shown on the dashboard.
- If the person tracker HTTP service is not running, `ros_adapter.py` starts
  `/home/elf/face_identity_rk3588/scripts/board_person_tracker_server.py` on
  `127.0.0.1:8102` and waits briefly before posting the current JPEG frame.

## Important ROS Safety Boundary

The cloud-model adapter starts `person_seek` / `person_follow`, which publish
motion intent to `/cmd_vel_raw`. It also ensures `obstacle_guard` is running and
configured to publish the filtered chassis command to `/cmd_vel`. The singleton
`Mcnamu_driver_X3` motor driver consumes `/cmd_vel`, talks to the Rosmaster
serial controller, and publishes measured/raw motion on `/vel_raw`; `base_node_X3`
then consumes `/vel_raw` for `/odom_raw`.

Do not replace this with direct `ros2 topic pub /cmd_vel_raw ...` testing.

The support stack follows `底盘启动.txt` from the chassis/perception handoff:

```text
ros2 run yahboomcar_bringup Mcnamu_driver_X3
ros2 run yahboomcar_base_node base_node_X3 --ros-args -p pub_odom_tf:=false
ros2 run depth_camera_perception fused_pose_monitor --ros-args -p web_port:=8091 -p publish_period_s:=0.02 -p yaw_rate_scale:=1.53 -p linear_x_scale:=0.9
ros2 launch depth_camera_perception obstacle_guard.launch.py dry_run:=false allow_bypass:=true use_fused_pose_bypass:=true input_cmd_vel_topic:=/cmd_vel_raw output_cmd_vel_topic:=/cmd_vel ...
```

Do not start multiple `Mcnamu_driver_X3` processes; duplicate low-level chassis
drivers previously caused `/vel_raw` / chassis command confusion. Cloud-model
checks for an existing `Mcnamu_driver_X3` process and only starts one if absent.
If an old `obstacle_guard` process is present without `output_cmd_vel_topic:=/cmd_vel`,
the adapter stops that stale guard before starting the corrected guard. Otherwise
the command chain can look healthy while the real motor driver is bypassed.

## Seek Arrival Prompt

Cloud-model should say "我找到他了。" / "我找到人了。" after `person_seek`
reaches the target. The completion monitor default timeout is 180 seconds and
can be overridden with:

```bash
export PERSON_SEEK_MONITOR_TIMEOUT_SEC=240
```

This timeout must be longer than a realistic room scan. In one real test,
`person_seek` reached `state=ARRIVED, reason=arrived` after `scan_elapsed_s`
was about 126.9 seconds; a 45-second cloud-model monitor expired too early and
missed the completion prompt.

The monitor also deliberately ignores startup-only `IDLE` status until it has
seen an active seek state. Some real launches briefly report `IDLE` before the
seek node starts searching; treating that as terminal caused the cloud-model
monitor to exit before the later `ARRIVED` status. When `ARRIVED` is observed,
cloud-model emits the prompt event and stops the active `person_seek` task so
the seek web monitor does not remain stuck on the completed run.

## Pending Verification

- `person_seek mode:=identity target_name:=tao` was verified by the user before
  this cloud-model integration.
- `person_follow mode:=identity target_name:=tao` was verified by the user after
  restoring the real chassis driver chain. Role `B` / `xiao` still needs a full
  room test if required.
- Obstacle interaction during identity follow can be tested later. The current
  launch path preserves the obstacle-guard chain so this can be verified without
  redesign.
- `observe_people_identity` starts the compatible
  `/home/elf/face_identity_rk3588/scripts/board_person_tracker_server.py`
  service on demand if `PERSON_TRACKER_URL` / `http://127.0.0.1:8102` is not
  already healthy. Depth output for the people list is intentionally left for a
  later step.
- Real robot observe check on 2026-06-24:
  - `/api/camera/snapshot` returned a platform-camera JPEG.
  - `observe_people()` returned one visible person as `tao` once
    (`confidence=0.477`, position `right`).
  - A subsequent frame returned the same position as unknown, so recognition is
    functional but still sensitive to face angle, distance, light, and the
    current single stored `tao` embedding.
- Real robot stop safety fix on 2026-06-30:
  - Symptom: after `别跟着了` / `stop`, cloud-model reported stop success, but
    the chassis could continue moving.
  - Evidence: the active driver subscribes to final `/cmd_vel`; the previous
    cloud-model stop path only published zero intent to `/cmd_vel_raw`.
  - Fix: `RosPersonTaskAdapter.stop_person_tasks()` now publishes repeated zero
    velocity to both `/cmd_vel_raw` and `/cmd_vel` before and after killing
    person seek/follow nodes.
  - Board smoke check captured a zero `/cmd_vel` sample immediately after
    `RosPersonTaskAdapter().control("stop", "nearest")`.
  - Normal follow/seek motion intent still goes through `/cmd_vel_raw` and the
    obstacle guard; direct `/cmd_vel` publication is only used for stop/cleanup.

## Lightweight Verification

Run from `/home/elf/cloud-model-safety-mainline`:

```bash
python3 -m pytest -q \
  person_tasks/test_roles.py \
  person_tasks/test_controller.py \
  person_tasks/test_intent.py \
  person_tasks/test_tools.py \
  person_tasks/test_ros_adapter.py \
  llm/test_person_task_tool.py \
  scripts/test_person_task_main_static.py \
  scripts/test_debug_runtime_person_tasks.py
```

Expected result after the stop safety fix:

```text
25 passed
```

Compile check:

```bash
python3 -m py_compile \
  main.py \
  llm/chat.py \
  debug_runtime.py \
  person_tasks/*.py
```

## Manual Debug Flow

Start cloud-model without ASR/LLM:

```bash
cd /home/elf/cloud-model-safety-mainline
./scripts/start_system.sh --cli-debug
```

Inside the prompt:

```text
debug> follow-me
debug> follow-a
debug> seek-a
debug> stop-person
debug> observe-people
```

Stop safety smoke check:

```bash
source /opt/ros/humble/setup.bash
source ~/ros2/install/setup.bash
export ROS_DOMAIN_ID=30
(timeout 6 ros2 topic echo /cmd_vel --once > /tmp/stop_cmd_vel_echo.txt 2>&1 &)
sleep 0.8
cd /home/elf/cloud-model-safety-mainline
PYTHONPATH=. python3 - <<'PY'
from person_tasks.ros_adapter import RosPersonTaskAdapter
print(RosPersonTaskAdapter().control("stop", "nearest"))
PY
cat /tmp/stop_cmd_vel_echo.txt
```

Expected `/cmd_vel` sample:

```text
linear.x: 0.0
angular.z: 0.0
```

Real voice validation should be done after the lightweight tests pass:

- `跟着我`
- `跟我走`
- `跟着角色A`
- `找一下角色B在哪里`
- `不要跟了`
- `你知道我是谁吗`
