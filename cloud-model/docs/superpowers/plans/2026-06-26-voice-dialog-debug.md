# Voice Dialog Debug Mode

## Context

The previous dialog debug mode was committed as:

- `42bc6df feat: add text-only dialog debug mode`

That mode is intentionally text-only. It does not start ASR, TTS, audio fix,
camera, ROS, dashboard, safety guard, chassis, or reading-arm resources.

## Problem

When testing conversation behavior, text-only debug cannot verify the real
wake-word, ASR, AEC, and TTS path. Running the full `main.py` path for every
ASR test also starts robot resources that are unrelated to speech debugging.

## New Mode

`scripts/start_system.sh --voice-dialog-debug` starts:

- real microphone capture through the configured `DEVICE_MIC`
- real KWS/ASR through `ASRProcessor`
- real TTS through `RealtimeSpeaker`
- real LLM conversation through `Conversation`
- no-op tool executor through `DialogDebugToolExecutor`

It does not start:

- platform camera
- safety guard
- dashboard
- resource scheduler
- ROS setup or camera nodes
- chassis, person-follow, or reading-arm system calls

## Commands

Text-only tool/prompt debug:

```bash
./scripts/start_system.sh --dialog-debug
```

Voice ASR/TTS dialog debug:

```bash
./scripts/start_system.sh --voice-dialog-debug
```

Dry-run validation:

```bash
./scripts/start_system.sh --voice-dialog-debug --dry-run --no-main --no-audio-fix
```

Regression tests:

```bash
python3 -m scripts.test_start_system_script
python3 -m py_compile voice_dialog_debug.py dialog_debug.py llm/dialog_debug_tools.py
```

## Expected Behavior

Say `你好小智` or `小智小智`, then speak a normal query. The console should show
KWS wake logs, `[识别] ...`, and a spoken LLM response. If a tool is called, the
tool result is simulated and must not touch robot hardware.

## ASR Turn-Collection Testing

The wake-session ASR redesign is tracked in:

- `docs/superpowers/plans/2026-06-26-wake-session-asr-turn-collector.md`

Use this voice debug mode to manually test the new turn collector without
starting camera, ROS, chassis, dashboard, safety, or reading-arm resources.
