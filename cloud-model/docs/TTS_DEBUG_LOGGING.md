# TTS Debug Logging

## Purpose

This file-only diagnostic log is used to distinguish three failure classes:

- the LLM stream ended before the expected final characters;
- TTS sentence buffering or flush lost characters;
- realtime TTS synthesis or `aplay` playback produced or played incomplete audio.

No new diagnostic lines are printed to the console.

## Log Path

Default:

```text
logs/tts_debug.jsonl
```

Override:

```bash
TTS_DEBUG_LOG=/tmp/tts_debug.jsonl ./scripts/start_system.sh
```

Disable:

```bash
TTS_DEBUG_LOG=0 ./scripts/start_system.sh
```

## Important Events

- `llm_delta`: every streamed text delta from the model.
- `llm_stream_done`: final LLM stream summary, including `finish_reason`, full text, and tail.
- `tts_feed`: text handed to the realtime speaker.
- `tts_queue_sentence`: sentence placed into the TTS synthesis queue.
- `tts_flush_buffer`: remaining text forced into TTS at stream end.
- `tts_synth_done`: realtime TTS returned PCM; includes bytes and estimated duration.
- `tts_audio_enqueue`: PCM, silence, or WAV queued for playback.
- `tts_play_pcm_done`: `aplay` playback result for PCM or sentence silence.
- `tts_play_wav_done`: `aplay` playback result for cached phrase WAV.
- `tts_cancel`: current queues and buffer when playback is interrupted.

## How To Diagnose A Truncated Final Word

Search the last turn by time:

```bash
tail -n 200 logs/tts_debug.jsonl
```

Then compare:

1. If `llm_stream_done.text_tail.tail` already misses the final character, the model stream ended early.
2. If `llm_stream_done` has the character but `tts_queue_sentence` or `tts_flush_buffer` does not, the TTS buffer path lost it.
3. If `tts_synth_done.text_tail.tail` has the character but playback stops early, check `pcm_bytes`, `duration_ms`, and `tts_play_pcm_done.returncode/error`.

`sentence_id` links `tts_queue_sentence`, `tts_synth_done`, `tts_audio_enqueue`, and `tts_play_pcm_done`.
