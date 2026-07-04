# Reading Mode Switch Latency TODO

## Current Baseline

- `normal -> reading`:
  1. pause safety,
  2. release platform camera (`stop` or `suspend`),
  3. start/check `~/ros2/start_reading_arm.sh`,
  4. `/reading/prepare`,
  5. `/reading/start`.
- `page-done`:
  - only stops arm tracking with `return_home=false`;
  - keeps reading resources held;
  - does not restore the platform camera;
  - keeps arm services alive for `next-page`.
- `reading -> normal`:
  1. `/reading/stop?return_home=1`,
  2. currently waits `SCHEDULER_ARM_RETURN_HOME_SETTLE_SEC`,
  3. stops arm services with `~/ros2/stop_reading_arm.sh`,
  4. restores platform camera,
  5. resumes safety.

## TODO 1: Replace Fixed Return-Home Wait With ROS Status

Problem: cloud-model currently waits a fixed `SCHEDULER_ARM_RETURN_HOME_SETTLE_SEC=3.0` after `return_home=1`. This is only a fallback because `/book/status` does not expose whether `servo_controller` has completed `STATE_EXIT_RETURN_HOME`.

Target:

- In `~/ros2/src/face_track`, expose return-home state from `servo_controller`.
- Add a ROS topic or service state such as:
  - `returning_home`
  - `return_home_complete`
  - `return_home_error`
- Let `arm_agent` include those fields in `/book/status`.
- Let cloud-model wait until `return_home_complete=true`, with a max timeout such as `SCHEDULER_ARM_RETURN_HOME_TIMEOUT_SEC=10`.
- Keep `SCHEDULER_ARM_RETURN_HOME_SETTLE_SEC` only as fallback.

Expected benefit:

- `exit-reading` waits exactly as long as the arm needs, not a guessed fixed delay.
- Slow return-home motions complete correctly.
- Fast motions do not waste time.

## TODO 2: Reduce `normal -> reading` Entry Latency

Observed issue: entering reading mode can take close to 10 seconds because platform camera release, reading-arm service startup, first-frame warmup, and arm prepare happen serially.

Candidate optimizations:

1. Prefer platform camera `suspend` mode over `stop`.
   - Implemented as the default in `scripts/start_system.sh`.
   - Use `SCHEDULER_PLATFORM_CAMERA_RELEASE_MODE=stop` only as a temporary fallback.
   - Avoids Orbbec process restart.
   - Still costs ROS CLI service-call time; future optimization can use a persistent rclpy client.

2. Keep arm services warm while not reading, but keep the reading camera inactive.
   - Requires ROS-side support to pause the arm camera capture or release `/dev/video*` without killing all arm nodes.
   - Would avoid `start_reading_arm.sh` process startup and first-frame warmup on every entry.
   - Must still release USB bandwidth when platform camera is active.

3. Parallelize independent work.
   - Safety pause and resource lease are cheap.
   - Platform suspend must happen before the reading camera starts if both conflict on USB bandwidth.
   - Arm return/prepare could potentially start immediately after suspend, while health checks continue.
   - Do not start reading camera before platform streams are actually suspended.

4. Optimize `prepare_reading`.
   - Current `/reading/prepare` waits for initial pose and settle.
   - If arm is already at/near initial pose, ROS side should return quickly.
   - Add status-based early exit instead of always running full motion.

Current implementation:

- Keep current safe sequence.
- Use `SCHEDULER_PLATFORM_CAMERA_RELEASE_MODE=suspend` by default from `scripts/start_system.sh`.
- Add ROS-side arm warm/sleep API only after return-home status is exposed.

## TODO 3: Keep `page-done` Lightweight

Status: implemented in cloud-model runtime scheduler. `RuntimeCoordinator` now tracks whether reading
resources are already held; after `page-done`, `next-page` reuses the existing reading resource lease,
keeps safety paused, and skips platform-camera release/toggle.

Decision: `page-done` should not restore platform camera and should not stop arm services.

Reason:

- The user is still in reading mode.
- Restoring platform camera and restarting reading resources between pages adds avoidable latency.
- `next-page` should reuse the existing arm services and already-suspended platform camera.

Required invariant:

- `page-done` may stop tracking with `return_home=false`.
- `page-done` must not call `~/ros2/stop_reading_arm.sh`.
- `page-done` must not call `resume_platform_camera.sh` or `start_platform_camera.sh`.
- `next-page` after `page-done` must not call `suspend_platform_camera.sh`, `stop_platform_camera.sh`,
  or `SafetyGuard.pause()` again.
- Expected scheduler logs on `next-page`: `reading_resources_reused`, `safety_pause_skipped`,
  `platform_camera_release_skipped`.
- Only `exit-reading`, startup failure, or switching to another high-priority mode restores normal resources.

## TODO 4: Long-Term Mode-State Optimization

Introduce explicit resource substates:

- `normal_active`: platform camera + safety active, arm services stopped.
- `reading_entry`: platform camera suspended, arm services starting/preparing.
- `reading_active`: arm tracking active, platform camera suspended, safety paused.
- `reading_page_pause`: arm service alive, tracking stopped, platform camera suspended, safety paused.
- `reading_exit`: return-home in progress, then arm service stop, then platform camera restore.

This makes it easier to reason about timing, logs, and failure recovery than a single `mode=reading` flag.
