# RK3588 AI Companion Robot

This repository contains a public release snapshot of an RK3588-based edge AI companion robot for child interaction, reading assistance, safety monitoring, person following, ROS2 robot control, and parent dashboard management.

## Repository Layout

```text
cloud-model/      Main voice Agent, dashboard backend/frontend, runtime scheduler, safety integration, reading mode, TTS/ASR glue, and tool calls.
ros2/             ROS2 workspace for platform camera, reading arm, chassis, obstacle guard, person finding/following, and related robot services.
hardware/pcb/     Reserved for PCB files and sensor expansion board documentation.
hardware/3d-models/
                  Reserved for mechanical and 3D model files.
docs/             Public release notes, dependency/license audit, and omitted asset manifest.
```

## Project Summary

The system runs on an RK3588 edge AI board and combines voice interaction, local RKNN visual inference, ROS2 robot control, a reading-arm camera, a platform RGB-D camera, environmental sensing, HDMI expression display, and a parent-facing web dashboard.

Main capabilities include:

- voice wake-up, ASR/VAD, TTS playback, interruption handling, and LLM tool calling;
- paper-book reading mode with reading-arm alignment, page capture, page rectification, and local book database matching;
- safety monitoring for falls, dangerous-object proximity, sleep-presence reminders, and event recording;
- person identity observation, person seeking, and person following through the ROS2 execution chain;
- parent dashboard for live view, history records, safety status, child/person settings, and robot control.

## Snapshot Sources

This monorepo is assembled from two local mainline snapshots:

- `cloud-model/`: source `cloud-model-safety-mainline`, commit `7d13dc2`.
- `ros2/`: source `ros2`, commit `2b0bdef`.

The original `.git/` directories are not embedded. This repository is intended as a public project delivery repository, not a full preservation of each source repository's history.

## Public Release Notes

This is a public-safe snapshot. Real cloud API keys are not included. Runtime keys must be supplied through environment variables:

```bash
export DASHSCOPE_API_KEY="your-key"
export DASHSCOPE_TTS_API_KEY="your-tts-key"  # optional; falls back to DASHSCOPE_API_KEY
```

Large third-party assets and binaries above the public release threshold were omitted from this initial upload. See `docs/OMITTED_LARGE_ASSETS.txt` and `docs/PUBLIC_RELEASE_NOTES.md`.

## License Status

The root project license is intentionally not declared yet. Several included ROS2 packages and third-party components have their own licenses or incomplete license declarations. Review `docs/DEPENDENCY_LICENSE_AUDIT.md` before changing repository visibility, redistributing binaries, or adding a root `LICENSE`.
