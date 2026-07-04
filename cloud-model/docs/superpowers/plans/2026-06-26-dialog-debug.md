# Dialog Debug Mode

## Purpose

`--dialog-debug` starts a text-only LLM conversation loop through
`scripts/start_system.sh` without touching robot resources. It is meant for
prompt, tool-calling, and multi-turn dialog testing when ASR, TTS, camera, ROS,
safety guard, dashboard, scheduler, chassis, and sensors should stay offline.

## Command

```bash
cd ~/cloud-model-safety-mainline
./scripts/start_system.sh --dialog-debug
```

Dry-run check:

```bash
./scripts/start_system.sh --dialog-debug --dry-run --no-main
```

## Behavior

- Runs `python3 dialog_debug.py`.
- Disables audio fix, platform camera startup, reading arm startup, dashboard,
  safety guard, and resource scheduler.
- Does not block on an existing `main.py` process because it does not use shared
  robot resources.
- All LLM tools are still exposed to test function-calling decisions, but
  `DialogDebugToolExecutor` returns deterministic no-op results:
  - camera/sensor tools return `available=false`;
  - person follow/seek control returns simulated success;
  - identity observation returns an empty person list.

## Tests

```bash
PYTHONPATH=. python3 llm/test_dialog_debug_tools.py
PYTHONPATH=. python3 scripts/test_start_system_script.py
python3 -m py_compile dialog_debug.py llm/chat.py llm/dialog_debug_tools.py
```
