"""Static checks that main.py wires person tasks into dashboard state."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"


def test_main_wires_person_task_controller_to_dashboard():
    text = MAIN.read_text(encoding="utf-8")
    assert "dashboard_state.set_person_task_controller(person_task_controller)" in text


def test_main_marks_dashboard_person_task_done_on_seek_arrival():
    text = MAIN.read_text(encoding="utf-8")
    assert "dashboard_state.mark_person_task_done(reason=\"arrived\", event=event)" in text


if __name__ == "__main__":
    test_main_wires_person_task_controller_to_dashboard()
    test_main_marks_dashboard_person_task_done_on_seek_arrival()
    print("ALL PASS")
