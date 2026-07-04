from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"


def test_main_exposes_person_task_tools_to_llm():
    text = MAIN.read_text(encoding="utf-8")

    assert "PERSON_TASK_TOOLS" in text
    assert "control_person_follow" in text
    assert "observe_people_identity" in text
    assert "跟着我" in text
    assert "角色A" in text


def test_main_has_deterministic_motion_intent_fallback():
    text = MAIN.read_text(encoding="utf-8")

    assert "parse_person_task_intent" in text
    assert "control_person_follow" in text


def test_main_injects_platform_camera_snapshot_into_person_observe_tool():
    text = MAIN.read_text(encoding="utf-8")

    assert "person_task_controller.set_snapshot_provider(safety_guard.camera_snapshot)" in text

def test_main_stops_person_tasks_on_shutdown():
    text = MAIN.read_text(encoding="utf-8")

    assert "shutdown_cleanup" in text
    assert 'person_task_controller.control("stop", "nearest")' in text


def test_main_speaks_seek_arrival_only_for_voice_started_task():
    text = MAIN.read_text(encoding="utf-8")

    assert "person_event_queue" in text
    assert "person_task_controller.set_event_handler" in text
    assert "process_person_task_events" in text
    assert '"seek_arrived"' in text
    branch_start = text.index("def process_person_task_events():")
    branch_end = text.index("def handle_interrupt():", branch_start)
    branch = text[branch_start:branch_end]
    assert 'voice_started = bool(active_person_task' in branch
    assert 'speaker.feed("我找到他了。")' in branch
    assert "speaker.flush()" in branch
    assert branch.index("voice_started") < branch.index("active_person_task = None")
    assert "mark_person_task_done" in branch


def test_main_stops_person_tasks_before_entering_reading_mode():
    text = MAIN.read_text(encoding="utf-8")
    branch_start = text.index("if is_reading_entry:")
    branch_end = text.index('conv.messages[0] = {', branch_start)
    branch = text[branch_start:branch_end]

    assert '_stop_person_tasks("before_reading")' in branch
    assert branch.index('_stop_person_tasks("before_reading")') < branch.index("_start_reading_tracking()")


def test_main_direct_person_task_skips_think_filler():
    text = MAIN.read_text(encoding="utf-8")
    branch_start = text.index("if (\n            person_intent")
    branch_end = text.index("time.sleep(0.3)", branch_start)
    branch = text[branch_start:branch_end]

    assert "think_filler()" not in branch


def test_main_starts_sleep_presence_worker_from_dashboard_state():
    text = MAIN.read_text(encoding="utf-8")

    assert "DASHBOARD_SLEEP_PRESENCE_ENABLED" in text
    assert "DASHBOARD_SLEEP_PRESENCE_INTERVAL_SEC" in text
    assert "sleep_presence_stop" in text
    assert "dashboard_state.refresh_sleep_presence_from_identity()" in text
    assert 'name="sleep-presence"' in text


def test_main_starts_chassis_support_stack_when_dashboard_chassis_is_enabled():
    text = MAIN.read_text(encoding="utf-8")

    assert "ensure_chassis_support_stack" in text
    assert "chassis_support" in text
    assert "chassis_control.config.enabled" in text


if __name__ == "__main__":
    test_main_exposes_person_task_tools_to_llm()
    print("test_main_exposes_person_task_tools_to_llm PASS")
    test_main_has_deterministic_motion_intent_fallback()
    print("test_main_has_deterministic_motion_intent_fallback PASS")
    test_main_injects_platform_camera_snapshot_into_person_observe_tool()
    print("test_main_injects_platform_camera_snapshot_into_person_observe_tool PASS")
    test_main_stops_person_tasks_on_shutdown()
    print("test_main_stops_person_tasks_on_shutdown PASS")
    test_main_speaks_seek_arrival_only_for_voice_started_task()
    print("test_main_speaks_seek_arrival_only_for_voice_started_task PASS")
    test_main_stops_person_tasks_before_entering_reading_mode()
    print("test_main_stops_person_tasks_before_entering_reading_mode PASS")
    test_main_direct_person_task_skips_think_filler()
    print("test_main_direct_person_task_skips_think_filler PASS")
    test_main_starts_sleep_presence_worker_from_dashboard_state()
    print("test_main_starts_sleep_presence_worker_from_dashboard_state PASS")
    test_main_starts_chassis_support_stack_when_dashboard_chassis_is_enabled()
    print("test_main_starts_chassis_support_stack_when_dashboard_chassis_is_enabled PASS")
    print("ALL PASS")
