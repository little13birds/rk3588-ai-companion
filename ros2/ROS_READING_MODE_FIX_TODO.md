# ROS Reading Mode Fix TODO

This file is the handoff entry for the agent working inside `~/ros2`.
It summarizes the ROS-side work needed by `~/cloud-model-safety-mainline`.

Current cloud-model branch/commit at handoff:

- Branch: `feat/safety-guard-mainline`
- Commit: `6e3141c fix: refresh dashboard sensors and show book corners`

Important boundary:

- Do not change `~/cloud-model-safety-mainline` from this ROS task unless explicitly asked.
- Do not overwrite existing uncommitted ROS changes.
- At the time this file was written, `~/ros2` already had uncommitted changes in:
  - `src/face_track/config/servo_params.yaml`
  - `src/face_track/face_track/servo_controller.py`
  - `src/face_track/face_track/test_servo_state_machine.py`
  - plus untracked YOLO model files in the repo root

## Cloud-Model Contract

`cloud-model` enters reading mode through `runtime_scheduler` and expects the reading arm stack to expose:

- `~/ros2/start_reading_arm.sh`
- `~/ros2/stop_reading_arm.sh`
- `arm_agent` HTTP server at `http://127.0.0.1:8642`
- `GET /book/status`
- `GET /frame.jpg?wait_ready=1&timeout=N`
- `POST /reading/prepare`
- `POST /reading/start`
- `POST /reading/stop?return_home=1`

Current cloud-model behavior:

- `normal -> reading`: platform camera is suspended, then `start_reading_arm.sh` is called on demand.
- `page-done -> next-page`: cloud-model keeps reading resources and arm services alive; this path should be fast.
- `reading -> normal`: cloud-model calls `/reading/stop?return_home=1`, currently waits a fixed fallback time, then stops arm services and resumes platform camera/safety.

## Priority 1: Expose Return-Home Completion Status

Problem:

- Cloud-model currently waits a fixed `SCHEDULER_ARM_RETURN_HOME_SETTLE_SEC=3.0` after `/reading/stop?return_home=1`.
- This is not robust: 3 seconds may be too short or too long.
- The root cause is that `/book/status` does not expose whether `servo_controller` has completed `STATE_EXIT_RETURN_HOME`.

Target:

- Add explicit return-home state from `servo_controller` to `arm_agent` and `/book/status`.
- Suggested fields:
  - `returning_home: bool`
  - `return_home_complete: bool`
  - `return_home_error: bool`
  - optional `return_home_state: "idle" | "active" | "complete" | "error"`

Suggested implementation:

- In `src/face_track/face_track/servo_controller.py`:
  - Add a status publisher, similar to `/book_prepare_status`, for return-home state.
  - Suggested topic: `/book_return_home_status` with `std_msgs/Int8`.
  - Suggested enum:
    - `0 = idle`
    - `1 = active`
    - `2 = complete`
    - `3 = error`
  - Publish active when `return_home_callback()` enters `STATE_EXIT_RETURN_HOME`.
  - Publish complete when the return-home motion reaches target.
  - Publish idle when a new prepare/start path supersedes return-home.

- In `src/face_track/face_track/arm_agent.py`:
  - Subscribe to `/book_return_home_status`.
  - Add fields to `snapshot()` and therefore `/book/status`.
  - Keep existing prepare fields unchanged for compatibility.

Tests:

- Add/extend pure tests under `src/face_track/face_track/test_servo_state_machine.py`.
- Add/extend arm-agent helper tests if needed.
- Manual check after implementation:

```bash
cd ~/ros2
./start_reading_arm.sh
curl -s http://127.0.0.1:8642/book/status | python3 -m json.tool
curl -s -X POST 'http://127.0.0.1:8642/reading/stop?return_home=1'
watch -n 0.2 "curl -s http://127.0.0.1:8642/book/status | python3 -m json.tool"
```

Acceptance:

- During return-home, `/book/status` shows `returning_home=true`.
- After physical return-home completes, `/book/status` shows `return_home_complete=true`.
- Cloud-model can later replace the fixed 3 second wait with a status wait.

## Priority 2: Make `/reading/prepare` Fast When Already Prepared

Problem:

- `normal -> reading` can spend avoidable time in `/reading/prepare`.
- If the arm is already near the initial pose and settled, full prepare motion should not run again.

Target:

- `/reading/prepare` should return quickly when:
  - joint positions are already within `prepare_tolerance` of `initial_pose`;
  - joint settle tracker says motion is settled;
  - no active return-home or conflicting state is running.

Suggested implementation:

- In `servo_controller.py`, make prepare state machine detect already-at-target and publish `PREPARE_COMPLETE` early.
- In `arm_agent.prepare_reading()`, continue waiting for `prepare_complete` and `settled`, but this should become nearly immediate when already prepared.

Tests:

- Add pure tests for prepare early-exit in `test_servo_state_machine.py`.
- Existing tests should still pass.

Acceptance:

- Calling `/reading/prepare` twice in a row should make the second call return quickly.
- Logs should clearly show whether prepare was a full motion or early complete.

## Priority 3: Warm/Sleep API For Reading Arm Camera

Problem:

- Starting `arm_agent` cold costs time because the camera and first frame need warmup.
- Keeping `arm_agent` permanently alive can conflict with the platform Orbbec camera on USB bandwidth if the reading camera keeps streaming.

Target:

- Keep ROS arm services warm without continuously owning the reading camera.
- Provide a ROS/HTTP API to pause/resume the reading camera capture while keeping nodes alive.

Possible API shape:

- `POST /camera/suspend`
  - stop reading camera capture loop;
  - release `cv2.VideoCapture`;
  - clear ready/frame state;
  - keep HTTP server and ROS subscriptions alive.

- `POST /camera/resume`
  - reopen configured camera;
  - wait for first frame;
  - report frame health through `/book/status`.

Possible `/book/status` fields:

- `camera_open: bool`
- `camera_suspended: bool`
- `frame_ok: bool`
- `frame_age_ms: number | null`
- `camera_source: string | int`
- `frame_width`, `frame_height`

Acceptance:

- Platform camera can run while reading camera is suspended.
- Reading mode can resume camera without restarting all ROS processes.
- If resume fails, `/book/status` reports a clear reason instead of silently failing `/frame.jpg`.

Notes:

- This should be done after Priority 1 unless the team explicitly wants to optimize entry latency first.
- Be careful with OpenCV `VideoCapture` lifecycle and thread locking.

## Priority 4: Improve Camera/Frame Health Diagnostics

Problem:

- Startup failures often look like `frame_unavailable`.
- It is hard to tell whether the camera device failed to open, no frame arrived, detection is failing, or the arm is still searching.

Target:

- Make `/book/status` sufficient for diagnosing camera and reading readiness.

Suggested fields:

- `camera_open`
- `frame_ok`
- `frame_age_ms`
- `last_frame_ts`
- `camera_source`
- `frame_width`
- `frame_height`
- `detect_found`
- `detect_fps`
- `last_infer_ms`
- `ready_reason` or `not_ready_reason`

Acceptance:

- When `/frame.jpg` is unavailable, `/book/status` explains why.
- `start_reading_arm.sh` health check failures include enough log/context to distinguish camera open failure vs no first frame.

## Priority 5: Keep Debug Page Useful

The existing `arm_agent` debug page is useful and should remain available.

Do not remove:

- `/`
- `/book/status`
- `/frame.jpg?debug=1`
- the existing prepare/start/stop/home buttons

Optional improvements:

- Show return-home state once Priority 1 is implemented.
- Show camera suspended/open/frame age once Priority 3/4 is implemented.

## Suggested Verification Commands

Pure tests:

```bash
cd ~/ros2
python3 src/face_track/face_track/test_servo_state_machine.py
python3 src/face_track/face_track/test_arm_agent_core.py
```

Reading arm scripts:

```bash
cd ~/ros2
./stop_reading_arm.sh
./start_reading_arm.sh
curl -s http://127.0.0.1:8642/book/status | python3 -m json.tool
curl -s -X POST http://127.0.0.1:8642/reading/prepare
curl -s -X POST http://127.0.0.1:8642/reading/start
curl -s -X POST 'http://127.0.0.1:8642/reading/stop?return_home=1'
```

Cloud-model side smoke test after ROS fixes:

```bash
cd ~/cloud-model-safety-mainline
./scripts/start_system.sh --cli-debug
```

Then in CLI debug:

```text
enter-reading
page-done
next-page
exit-reading
```

Expected:

- `enter-reading` reaches `reading_started`.
- `page-done` does not restore platform camera.
- `next-page` prints `reading_resources_reused`.
- `exit-reading` waits for actual ROS return-home completion once cloud-model is updated to consume the new status.

## Do Not Do In This ROS Task

- Do not modify parent dashboard, safety guard, LLM/TTS, or cloud-model scheduler code.
- Do not bypass obstacle guard by publishing directly to `/cmd_vel` from dashboard or reading code.
- Do not overwrite existing uncommitted changes in `~/ros2`.
- Do not commit unrelated YOLO model files in the ROS repo root.
