from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEBUG = ROOT / "debug_runtime.py"


def test_debug_runtime_exposes_person_task_commands():
    text = DEBUG.read_text(encoding="utf-8")

    assert "follow-me" in text
    assert "follow-a" in text
    assert "seek-a" in text
    assert "stop-person" in text
    assert "observe-people" in text
    assert "execute_person_tool" in text


def test_debug_runtime_stops_stale_person_tasks_on_startup_and_shutdown():
    text = DEBUG.read_text(encoding="utf-8")

    assert "stop_stale_person_tasks" in text
    assert "startup_cleanup" in text
    assert "shutdown_cleanup" in text
