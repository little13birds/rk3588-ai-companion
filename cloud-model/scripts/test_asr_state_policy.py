"""Static checks for ASR wake/sleep policy after task transitions.

Run from repo root:
    python3 scripts/test_asr_state_policy.py
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
RECOGNIZER = ROOT / "asr" / "recognizer.py"


def _source() -> str:
    return MAIN.read_text(encoding="utf-8")


def _recognizer_source() -> str:
    return RECOGNIZER.read_text(encoding="utf-8")


def _slice_between(source: str, start_marker: str, end_marker: str) -> str:
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def test_interrupt_words_are_hard_stop_not_reading_pause():
    source = _source()
    on_wake = _slice_between(source, "def _on_wake", "def _on_vad")

    assert 'MODE == "reading" and kw in WAKE_WORDS' in on_wake
    assert 'interrupt_reason = "reading_pause"' in on_wake
    assert 'elif kw in INTERRUPT_WORDS:' in on_wake
    assert 'interrupt_reason = "hard_stop"' in on_wake
    assert 'MODE == "reading" and (kw in WAKE_WORDS or kw in INTERRUPT_WORDS)' not in on_wake


def test_hard_stop_cancel_branch_forces_sleep_not_awake():
    source = _source()
    branch = _slice_between(
        source,
        'if cancel_event.is_set():',
        'dashboard_state.add_conversation("robot", response_text)',
    )

    hard_stop_pos = branch.index('if reason == "hard_stop":')
    hard_stop_branch = branch[hard_stop_pos:branch.index('if MODE == "reading" and reason == "reading_pause"', hard_stop_pos)]
    assert "asr.sleep()" in hard_stop_branch
    assert "auto_awake_requested = False" in hard_stop_branch
    assert "asr.force_awake()" not in hard_stop_branch


def test_handle_interrupt_requests_sleep_for_hard_stop_immediately():
    source = _source()
    handler = _slice_between(source, "def handle_interrupt", "# ── 主循环")

    assert "interrupt_reason" in handler
    assert 'if interrupt_reason == "hard_stop":' in handler
    hard_stop_branch = handler[
        handler.index('if interrupt_reason == "hard_stop":'):
        handler.index("# 6. 重置所有共享状态")
    ]
    assert "asr.sleep()" in hard_stop_branch


def test_reading_pause_still_enters_awake_for_followup_command():
    source = _source()
    branch = _slice_between(
        source,
        'if MODE == "reading" and reason == "reading_pause":',
        'if MODE == "reading":',
    )

    assert "asr.force_awake()" in branch
    assert "auto_awake_requested = True" in branch


def test_reading_exit_defaults_to_sleep():
    source = _source()
    branch = _slice_between(source, "# ── 读书模式退出 ──", "if reading_chat_transition:")

    assert "asr.sleep()" in branch
    assert "auto_awake_requested = False" in branch
    assert "auto_awake_requested = True" not in branch


def test_person_task_direct_intent_defaults_to_sleep():
    source = _source()
    branch = _slice_between(source, "if (\n            person_intent", "time.sleep(0.3)")

    assert "asr.sleep()" in branch
    assert "auto_awake_requested = False" in branch
    assert "auto_awake_requested = True" not in branch


def test_reading_start_failure_defaults_to_sleep_after_prompt():
    source = _source()
    branch = _slice_between(
        source,
        'if _scheduler_enabled() and not reading_tracking_ok:',
        'conv.messages[0] = {',
    )

    assert 'speaker.feed("读书摄像头还没准备好，请检查机械臂服务。")' in branch
    assert "speaker.flush()" in branch
    assert "speaker.wait()" in branch
    assert "asr.sleep()" in branch
    assert "auto_awake_requested = False" in branch
    assert "auto_awake_requested = True" not in branch


def test_idle_sleep_does_not_trigger_while_vad_is_speaking():
    source = _source()
    recognizer = _recognizer_source()

    assert "def is_speaking(self)" in recognizer
    assert "not asr.is_speaking()" in source
    idle_branch = _slice_between(source, "# 空闲超时 → 休眠", "except KeyboardInterrupt:")
    assert idle_branch.index("not asr.is_speaking()") < idle_branch.index("time.time() - idle_since")


def test_vad_edges_refresh_idle_timer_after_long_speech():
    source = _source()
    branch = _slice_between(source, "def _on_vad", "def _sync_dashboard_runtime")

    assert "idle_since = time.time()" in branch
    assert "if speaking:" not in branch


if __name__ == "__main__":
    test_interrupt_words_are_hard_stop_not_reading_pause()
    print("test_interrupt_words_are_hard_stop_not_reading_pause PASS")
    test_hard_stop_cancel_branch_forces_sleep_not_awake()
    print("test_hard_stop_cancel_branch_forces_sleep_not_awake PASS")
    test_handle_interrupt_requests_sleep_for_hard_stop_immediately()
    print("test_handle_interrupt_requests_sleep_for_hard_stop_immediately PASS")
    test_reading_pause_still_enters_awake_for_followup_command()
    print("test_reading_pause_still_enters_awake_for_followup_command PASS")
    test_reading_exit_defaults_to_sleep()
    print("test_reading_exit_defaults_to_sleep PASS")
    test_person_task_direct_intent_defaults_to_sleep()
    print("test_person_task_direct_intent_defaults_to_sleep PASS")
    test_reading_start_failure_defaults_to_sleep_after_prompt()
    print("test_reading_start_failure_defaults_to_sleep_after_prompt PASS")
    test_idle_sleep_does_not_trigger_while_vad_is_speaking()
    print("test_idle_sleep_does_not_trigger_while_vad_is_speaking PASS")
    test_vad_edges_refresh_idle_timer_after_long_speech()
    print("test_vad_edges_refresh_idle_timer_after_long_speech PASS")
    print("ALL PASS")
