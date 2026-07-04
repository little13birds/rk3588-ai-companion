# HDMI Eye GUI Mainline Integration Notes

## Goal

Bring the existing HDMI eye expression renderer back into the current
`cloud-model-safety-mainline` master without merging the stale
`feat/eyes-integration` branch.

## Scope

- Reuse the old `eye_engine` renderer and state files.
- Add `display/eye_controller.py` as a safe adapter.
- Keep the GUI optional and non-fatal: if HDMI, DRM/KMS, or Pygame fails, the
  voice assistant continues to run.
- Enable the feature by default from `scripts/start_system.sh` with
  `EYE_GUI_ENABLED=1`.
- Do not change ROS nodes, camera scripts, dashboard APIs, or scheduler resource
  ownership in this pass.

## Runtime Mapping

- startup / sleep: `sleepy`
- wake / listening: `neutral`
- processing / mode transitions: `thinking`
- fixed speech / short replies: `happy`
- reading mode: `reading`
- person seek/follow: `navigation`
- wake interrupt / hard stop: blink

## Follow-up: Visible Thinking Feedback

Initial integration exposed two timing issues during real interaction:

- Repeated `thinking` updates did not restart the renderer animation because
  the render loop only watched expression name changes.
- `draw_think()` starts with a blink/focus transition before the loading
  indicator, so short gaps such as “稍等一下” → actual audio playback looked
  visually idle.

The renderer now treats `EyeState.trigger_time` changes as animation reset
events and starts `thinking` from the visible loading phase. Normal LLM
streaming also switches the eye display to `speaking` when the first playable
text is emitted, so long “thinking” waits and actual speech are visually
distinct.

## Scheduler Position

For now, the HDMI eye renderer is not modeled as a scheduler-owned resource.
It does not use USB bandwidth, robot motion, camera, speaker, microphone, or
NPU. If future tests show DRM/KMS conflicts with another local display process,
add an `hdmi_display` resource to `runtime_scheduler/modes.py` and acquire it
from normal, reading, and person-task modes.

## Verification

Run on the board from `~/cloud-model-safety-mainline`:

```bash
python3 -m display.test_eye_controller
python3 scripts/test_eye_gui_integration.py
python3 -m py_compile main.py llm/chat.py display/eye_controller.py eye_engine/__init__.py eye_engine/eye_state.py eye_engine/eye_renderer.py
EYE_GUI_ENABLED=1 ./scripts/start_system.sh --dry-run --no-main | grep -E 'EYE_GUI_ENABLED|dry-run'
```

Then restart normally:

```bash
./scripts/start_system.sh --stop
./scripts/start_system.sh
```
