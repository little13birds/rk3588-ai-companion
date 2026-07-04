# Startup Audio and Reading Stability Notes

Date: 2026-06-28

Stable checkpoints:

- `cloud-model-safety-mainline`: `stable-reading-switch-20260628` -> `a3868d4`
- `ros2`: `stable-reading-switch-20260628` -> `a441e4b`

## ALSA Default Device Fix

The startup script calls `/mnt/sdcard/reconstruct/fix_audio.sh`.

The old script wrote this broken ALSA default:

```text
pcm.!default {
    type asym
    capture.pcm "plughw:Audio,0"
    playback.pcm "default"
}
```

That makes `playback.pcm` point back to `pcm.!default`, causing:

```text
ALSA lib conf.c:5783:(snd1_config_check_hop) Too many definition levels (looped?)
```

The current verified config is:

```text
pcm.!default {
    type asym
    capture.pcm "plughw:Device,0"
    playback.pcm "plughw:Device,0"
}

ctl.!default {
    type hw
    card Device
}
```

This matches the external USB microphone/speaker card shown by `/proc/asound/cards`:

```text
Device [USB PnP Sound Device]
```

Verification commands:

```bash
/mnt/sdcard/reconstruct/fix_audio.sh
arecord -D default -q -d 1 -f S16_LE -r 16000 -c 1 /tmp/asound_default_test.wav
timeout 3 aplay -D default -q /tmp/asound_default_test.wav
```

Also verified through the cloud-model startup path:

```bash
cd ~/cloud-model-safety-mainline
./scripts/start_system.sh --no-main --no-platform-camera --no-safety --no-dashboard --no-scheduler
arecord -D default -q -d 1 -f S16_LE -r 16000 -c 1 /tmp/asound_start_system_test.wav
timeout 3 aplay -D default -q /tmp/asound_start_system_test.wav
```

## HDMI Eye GUI Must Not Hold the Audio Device

On 2026-06-30 the voice wake path could recognize speech, but the wake reply
and TTS playback failed with:

```text
aplay: main:831: audio open error: Device or resource busy
```

The root cause was not the ALSA default file and not TTS synthesis. The HDMI eye
GUI used `pygame.init()`, which initializes pygame mixer/audio as well as the
display stack. On this board that opened `/dev/snd/pcmC5D0p` from the external
USB PnP sound card. TTS then tried to start `aplay -D plughw:Device,0` and
failed because the playback PCM was already held by the eye GUI process.

The older pre-GUI audio path avoided this conflict in two ways at different
times:

- early commits used `DEVICE_SPK="plughw:1,0"` for the board `rockchip-nau8822`
  speaker path;
- another working commit used a camera microphone and the USB PnP playback
  device, so capture and playback were not both forced through the same device.

Current rule:

- The eye GUI is visual only.
- `eye_engine/eye_renderer.py` must set `SDL_AUDIODRIVER=dummy`.
- It must call `pygame.display.init()` and `pygame.font.init()`, not
  `pygame.init()`.
- Do not call `pygame.mixer.init()` in the eye GUI.

Board verification used:

```bash
cd ~/cloud-model-safety-mainline
python3 -m py_compile eye_engine/eye_renderer.py
python3 scripts/test_eye_audio_isolation.py

# With EyeDisplayController running:
fuser -v /dev/snd/* 2>&1 || true
timeout 3 aplay -q -D plughw:Device,0 audio/fillers/wake_1.wav
python3 -c 'from tts.realtime_tts import RealtimeSpeaker; sp=RealtimeSpeaker(voice="Cherry"); sp.queue_wav("audio/fillers/wake_1.wav"); sp.wait(); print("queue_wav ok")'
```

Expected result:

- `fuser` should not show the eye GUI Python process holding
  `/dev/snd/pcmC5D0p`;
- direct `aplay` should return `rc=0`;
- `RealtimeSpeaker.queue_wav()` should complete.

## Reading Mode Stability

Reading mode currently keeps the reading arm service on-demand:

- normal startup starts the platform Orbbec camera;
- entering reading mode suspends the platform camera in `ROS_DOMAIN_ID=30`;
- the scheduler starts the reading arm service;
- `arm_agent` waits for camera frame health;
- `/reading/prepare` is retried until the servo acknowledges it.

The arm serial/servo path itself is not the USB bandwidth-heavy part. The bandwidth-sensitive conflict is between the platform Orbbec camera and the reading USB camera.

Potential future optimization:

- optionally prestart `roarm_driver` and `servo_controller`;
- keep `arm_agent` camera capture on-demand;
- only enable this when the arm is powered, otherwise startup will become noisier.

### Next Page Prompt Gate

The fixed next-page prompt is allowed only after a successful reading turn:

- `llm/chat.py` must record the current reading `take_photo` result in
  `Conversation.last_reading_tool_result`;
- `capture_ok` must be true, meaning the reading camera returned image data for
  this turn;
- `reading_mode.classify_reading_turn()` must classify the model response as
  successful, not a retry/failure response;
- if the model already asked `继续读下一页吗`, the system must not play the fixed
  prompt again.

Regression check:

```bash
cd ~/cloud-model-safety-mainline
python3 scripts/test_reading_next_page_gate.py
python3 scripts/test_reading_interrupt_flow.py
```

### Reading Fixed Phrase Timing

Reading-mode phrases are split by state so that page continuation does not sound
like a fresh mode entry:

- entering reading mode: `main.py` says `正在进入读书模式，请稍候。`;
- continuing after a successful page: `reading_next_page_filler()` says
  `好的，继续读下一页。`;
- retrying after a failed or unclear page: `reading_retry_filler()` says
  `好的，我再试一次。`;
- the `take_photo` tool layer only uses the neutral `reading_photo_filler()`
  phrase `我看一下。`.

Do not put stateful phrases such as `我们开始读书` inside `llm/chat.py`
`take_photo`; that layer cannot know whether the request is first entry,
next-page continuation, or retry.

Regression check:

```bash
cd ~/cloud-model-safety-mainline
python3 scripts/test_reading_phrase_flow.py
python3 scripts/test_reading_interrupt_flow.py
```

### Next Page Must Not Re-prepare The Arm

The ROS reading-arm state machine already has a page-continuation path:

- `/reading/stop` with `return_home=0` leaves `servo_controller` in
  `STATE_NEXT_PAGE_WAIT` and preserves the current pose;
- `/reading/start` from that state enters `STATE_NEXT_PAGE_FINE_ALIGN` if the
  book is still detected;
- if the book is lost, ROS falls back to `STATE_NEXT_PAGE_LOCAL_SEARCH` around
  the preserved pose before using the wider startup search.

Cloud-model must not call `/reading/prepare` on this next-page path. Calling
prepare resets the ROS state to `STATE_IDLE` and moves the arm back to
`initial_pose`, which breaks the local next-page behavior.

Expected scheduler behavior:

- first reading entry: health check -> `/reading/prepare` -> `/reading/start`;
- page pause: `/reading/stop` with `return_home=0`, keep reading resources;
- next page with reused resources: health check -> skip prepare ->
  `/reading/start`;
- final exit: `/reading/stop?return_home=1`.

Regression check:

```bash
cd ~/cloud-model-safety-mainline
python3 -m runtime_scheduler.test_coordinator
```
