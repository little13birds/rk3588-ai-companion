# Platform Camera Suspend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional platform-camera suspend/resume path for reading mode, using existing Orbbec ROS2 toggle services instead of killing and restarting the platform camera process.

**Architecture:** Keep the existing process stop/start scripts as the default and fallback. Add suspend/resume scripts, extend `PlatformCameraAdapter` with `release_for_reading()` and `restore_after_reading()`, and let `RuntimeCoordinator` call those abstractions. The feature is controlled by `SCHEDULER_PLATFORM_CAMERA_RELEASE_MODE=stop|suspend`.

**Tech Stack:** Bash startup scripts, Python runtime scheduler adapters/tests, ROS2 `std_srvs/srv/SetBool`, Orbbec `/camera/toggle_color` and `/camera/toggle_depth`.

---

## Files

- Create: `scripts/suspend_platform_camera.sh`
- Create: `scripts/resume_platform_camera.sh`
- Modify: `runtime_scheduler/adapters/platform_camera.py`
- Modify: `runtime_scheduler/coordinator.py`
- Modify: `runtime_scheduler/test_platform_camera_adapter.py`
- Modify: `runtime_scheduler/test_coordinator.py`
- Modify: `scripts/test_platform_camera_scripts.py`
- Modify: `scripts/test_start_system_script.py`
- Modify: `scripts/start_system.sh`
- Modify: `CLAUDE.md`
- Create: `docs/superpowers/specs/2026-06-24-platform-camera-suspend-design.md`

## Behavior

- Default remains `SCHEDULER_PLATFORM_CAMERA_RELEASE_MODE=stop`.
- Suspend mode calls:
  - `/camera/toggle_color {data: false}`
  - `/camera/toggle_depth {data: false}`
- Resume mode calls:
  - `/camera/toggle_depth {data: true}`
  - `/camera/toggle_color {data: true}`
- If suspend/resume fails and `SCHEDULER_PLATFORM_CAMERA_SUSPEND_FALLBACK_TO_STOP=1`, fall back to the existing stop/start scripts.
- Page pause does not restore platform camera. It only stops arm tracking while keeping reading resources held.

## Verification Commands

Run locally before syncing to the board:

```bash
python3 -m runtime_scheduler.test_platform_camera_adapter
python3 -m runtime_scheduler.test_coordinator
python3 -m scripts.test_platform_camera_scripts
python3 -m scripts.test_start_system_script
```

Run on the board after syncing:

```bash
cd ~/cloud-model-safety-mainline
python3 -m runtime_scheduler.test_platform_camera_adapter
python3 -m runtime_scheduler.test_coordinator
python3 -m scripts.test_platform_camera_scripts
python3 -m scripts.test_start_system_script
```

Manual ROS stream validation:

```bash
cd ~/cloud-model-safety-mainline
./scripts/start_platform_camera.sh
PLATFORM_CAMERA_SUSPEND_VERIFY_NO_FRAME=1 ./scripts/suspend_platform_camera.sh
~/ros2/start_reading_arm.sh
python3 -c "from arm import agent_client; print(agent_client.health(require_frame=True, timeout=1.5))"
~/ros2/stop_reading_arm.sh
PLATFORM_CAMERA_RESUME_REQUIRE_DEPTH_FRAME=1 ./scripts/resume_platform_camera.sh
```

CLI debug validation:

```bash
cd ~/cloud-model-safety-mainline
SCHEDULER_PLATFORM_CAMERA_RELEASE_MODE=suspend ./scripts/start_system.sh --cli-debug
```

Then run:

```text
status
enter-reading
status
page-done
next-page
exit-reading
status
quit
```

Expected log markers:

- Enter reading: `platform_camera_release_begin`, `suspend_begin`, `suspend_done`, `platform_camera_release_ok`.
- Page done: no platform camera restore.
- Exit reading: `platform_camera_restore_begin`, `resume_begin`, `resume_done`, `platform_camera_restore_ok`, `normal_restored`.
- Repeated page transitions: repeated suspend accepts `Already OFF`, repeated resume accepts `Already ON`.

## Rollback

Use the old path without reverting code:

```bash
SCHEDULER_PLATFORM_CAMERA_RELEASE_MODE=stop ./scripts/start_system.sh --cli-debug
```

Disable platform camera release entirely only for narrow debugging:

```bash
SCHEDULER_READING_STOPS_PLATFORM_CAMERA=0 ./scripts/start_system.sh --cli-debug
```
