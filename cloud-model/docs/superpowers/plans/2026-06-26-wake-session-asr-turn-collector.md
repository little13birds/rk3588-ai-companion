# Wake-Session ASR Turn Collector

## Problem

The old AWAKE path decoded the segment produced by sherpa VAD directly:

```text
VAD segment ready -> decode seg.samples -> on_result
```

When the user spoke immediately after the wake reply, VAD could become active
late enough to drop the beginning of the command. This was visible as commands
like `退出跟随模式` being recognized as only `跟随模式`.

## Current Design

`asr.turn_detector.AwakeTurnDetector` now handles wake-session turn collection.

Flow:

1. `ASRProcessor` enters AWAKE mode and resets the turn detector.
2. While VAD is still false, the detector keeps a small rolling audio pre-roll.
3. Once VAD becomes true, the detector locks that pre-roll and continues
   collecting speech audio.
4. After VAD returns false for the configured trailing silence window, the full
   collected turn is decoded by SenseVoice.
5. If no speech starts within the timeout, ASR returns to SLEEP/KWS.

The detector only starts after AWAKE mode, so it does not prepend the wake word
audio from SLEEP/KWS mode.

## Tunable Environment Variables

Defaults:

```bash
ASR_KWS_KEYWORDS=asr/kws_keywords.txt
ASR_WAKE_PREROLL_MS=500
ASR_MIN_SPEECH_MS=200
ASR_NO_SPEECH_SLEEP_MS=5000
ASR_MAX_TURN_MS=15000
```

`ASR_KWS_KEYWORDS` can still override the keyword file. If unset, the project
uses `asr/kws_keywords.txt`, which includes wake words and stop/interruption
phrases such as `停`, `暂停`, `安静`, and `停止`.

Trailing silence still comes from the existing `silence_timeout_ms` passed to
`ASRProcessor`; `main.py` currently uses `800`.

## Test Commands

Fast logic tests:

```bash
python3 -m asr.test_turn_detector
python3 -m asr.test_recognizer_turn_detector_integration
```

Startup/script regression:

```bash
python3 -m scripts.test_start_system_script
```

Syntax/model-load smoke check:

```bash
python3 -m py_compile asr/turn_detector.py asr/recognizer.py voice_dialog_debug.py
timeout -s INT 18 ./scripts/start_system.sh --voice-dialog-debug --no-audio-fix
```

## Manual Validation

Use the voice debug entry:

```bash
./scripts/start_system.sh --voice-dialog-debug
```

Test cases:

- Wake, then immediately say `退出跟随模式`.
- Wake, then immediately say `你知道我是谁吗`.
- Wake, say nothing for 5 seconds, confirm it returns to sleep.

Expected logs include:

```text
[AWAKE] turn=ready reason=trailing_silence samples=...
```

or, for no speech:

```text
[AWAKE] turn=timeout reason=no_speech
```
