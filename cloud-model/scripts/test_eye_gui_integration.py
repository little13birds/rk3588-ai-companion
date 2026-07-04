"""Static checks for HDMI eye GUI integration.

Run from the repository root with:
    python3 scripts/test_eye_gui_integration.py
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_main_starts_eye_display_and_updates_modes():
    source = _read("main.py")

    assert "from display.eye_controller import EyeDisplayController" in source
    assert "eye_display = EyeDisplayController.from_env()" in source
    assert "eye_display.start()" in source
    assert 'startup.record("eye_display"' in source
    assert 'eye_display.set_mode("listen")' in source
    assert 'eye_display.set_mode("thinking")' in source
    assert 'eye_display.set_mode("reading")' in source
    assert 'eye_display.set_mode("following")' in source
    assert 'eye_display.set_mode("sleep")' in source
    assert "eye_display.blink()" in source


def test_start_script_enables_eye_gui_by_default():
    source = _read("scripts/start_system.sh")

    assert 'export EYE_GUI_ENABLED="${EYE_GUI_ENABLED:-1}"' in source
    assert 'log "EYE_GUI_ENABLED=${EYE_GUI_ENABLED}"' in source


def test_thinking_animation_restarts_in_visible_phase():
    engine_source = _read("eye_engine/__init__.py")
    renderer_source = _read("eye_engine/eye_renderer.py")

    assert "THINKING_VISIBLE_START" in renderer_source
    assert "current_trigger_time" in engine_source
    assert "trigger_time != current_trigger_time" in engine_source
    assert 'current_expr == "thinking"' in engine_source


def test_streaming_answer_switches_eye_to_speaking():
    main_source = _read("main.py")
    chat_source = _read("llm/chat.py")

    assert "on_stream_start" in chat_source
    assert "self.on_stream_start()" in chat_source
    assert 'on_stream_start=lambda: eye_display.set_mode("speaking")' in main_source


if __name__ == "__main__":
    test_main_starts_eye_display_and_updates_modes()
    test_start_script_enables_eye_gui_by_default()
    test_thinking_animation_restarts_in_visible_phase()
    test_streaming_answer_switches_eye_to_speaking()
    print("test_eye_gui_integration PASS")
