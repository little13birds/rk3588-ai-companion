# Platform Camera Suspend Design

## Goal

Reduce reading-mode camera handoff latency and USB bandwidth contention by suspending the platform Orbbec RGB/depth streams instead of killing and restarting the platform camera process.

## Context

The existing scheduler enters reading mode by pausing safety, stopping the platform camera process, starting the reading arm service, and then restarting the platform camera when reading exits. That process-level restart is robust but slow because the Orbbec launch path waits for publishers, waits for a real frame, and may restart on bad frame health.

The Orbbec ROS2 driver already exposes `/camera/toggle_color`, `/camera/toggle_depth`, and `/camera/toggle_ir` services. The driver implementation calls `pipeline_->stop()`, updates the stream enable flag, rebuilds profiles, and restarts the remaining streams. Manual validation on the board showed:

- color/depth frames are available before suspend.
- `toggle_color false` and `toggle_depth false` return success.
- color/depth frame probes time out while suspended.
- the reading arm can start and obtain frames while platform streams are suspended.
- `toggle_depth true` and `toggle_color true` restore platform frames without relaunching the Orbbec node.

## Design

Add a second platform camera release mode to cloud-model:

- `stop`: existing behavior, run `scripts/stop_platform_camera.sh` and `scripts/start_platform_camera.sh`.
- `suspend`: new behavior, run new suspend/resume scripts that call Orbbec toggle services.

The mode is controlled by `SCHEDULER_PLATFORM_CAMERA_RELEASE_MODE`, defaulting to `stop` for conservative compatibility. Start scripts print this value so test logs clearly show the chosen path. The runtime coordinator calls `PlatformCameraAdapter.release_for_reading()` when entering reading mode and `restore_after_reading()` when reading exits or reading startup fails. For `stop` mode these methods preserve the old behavior; for `suspend` mode they call the new suspend/resume scripts.

## Failure Handling

Suspend/resume scripts return non-zero when ROS2 is unavailable, the toggle service is missing, or a service call fails. Repeated suspend/resume is idempotent: Orbbec `Already OFF` and `Already ON` responses are accepted so `page-done -> next-page` does not fall back unnecessarily.

The production path keeps checks short. `suspend_platform_camera.sh` does not wait for a no-frame timeout unless `PLATFORM_CAMERA_SUSPEND_VERIFY_NO_FRAME=1`. `resume_platform_camera.sh` waits for the RGB frame by default; depth frame verification is optional via `PLATFORM_CAMERA_RESUME_REQUIRE_DEPTH_FRAME=1`.

The adapter can fall back to the existing process stop/start path when `SCHEDULER_PLATFORM_CAMERA_SUSPEND_FALLBACK_TO_STOP=1`, which is enabled by default. This keeps the old stable path available during field testing.

## Verification

Local tests cover:

- adapter mode selection from environment.
- suspend mode using release/restore scripts instead of stop/start scripts.
- fallback to stop/start when suspend/resume fails.
- coordinator calling release/restore abstractions rather than hard-coded stop/start.
- start script dry-run exposing the release mode.

Board validation uses `./scripts/start_system.sh --cli-debug` and manual commands:

- `enter-reading` should log platform camera suspend events before arm startup.
- `page-done` should not restore platform camera.
- `next-page` should reuse reading resources.
- `exit-reading` should stop the arm and resume platform RGB/depth streams.
