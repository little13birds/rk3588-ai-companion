# Voice Wake Unresponsive TODO

## User Report

On 2026-06-27, after starting cloud-model with `./scripts/start_system.sh`, the
user repeatedly said the wake word but the assistant did not respond.

## Current Evidence

- The expected wake words are still `你好小智` and `小智小智`.
- The external USB microphone/speaker device is still enumerated as
  `USB PnP Sound Device`.
- A quick board check during the report found no live `python3 main.py` process
  and `http://127.0.0.1:8080/api/health` did not respond. This may mean the
  reported run had already exited, or that the command was not running in the
  terminal being inspected.
- Older logs under `logs/cloud-model_20260624-*.log` still show KWS working, so
  do not assume the KWS model is corrupt before checking the live run.

## Next Investigation Checklist

1. Start from a clean foreground run:
   ```bash
   cd ~/cloud-model-safety-mainline
   ./scripts/start_system.sh --stop
   ./scripts/start_system.sh
   ```
2. Confirm the live process exists:
   ```bash
   ps -eo pid,ppid,stat,etime,cmd | grep -E "python3 .*main.py|python3 main.py" | grep -v grep
   curl -s http://127.0.0.1:8080/api/health
   ```
3. Confirm startup reaches:
   - `[ASR] enter SLEEP/KWS reason=initial`
   - `[system] event=ready message=初始化完成，可以开始测试。`
4. Confirm audio capture is using the intended external USB device, not board
   mic, camera mic, or Orbbec audio:
   ```bash
   arecord -l
   cat /proc/asound/cards
   ```
   Expected device: `USB PnP Sound Device`.
5. Run a short raw capture/listen or ASR debug check before modifying code.
6. If KWS still does not react while `main.py` is running, enable diagnostic
   progress:
   ```bash
   ASR_KWS_PROGRESS_EVERY=50 ./scripts/start_system.sh
   ```
   Check whether KWS chunk counts advance and whether the microphone queue is
   receiving data.

## Constraints

- Do not change KWS thresholds or model files before verifying that the live
  microphone stream is reaching ASR.
- Do not enumerate/select board, camera, or Orbbec microphones as the default
  input; the intended device is the external USB microphone/speaker.
- Be aware that default chassis support startup may increase CPU/ROS load, but
  this should be tested as a hypothesis only after confirming the live ASR input
  path.
