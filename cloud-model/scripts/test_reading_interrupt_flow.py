"""Static checks for reading-mode wake interrupt flow.

Run from repo root:
    python3 scripts/test_reading_interrupt_flow.py
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
CHAT = ROOT / "llm" / "chat.py"
FILLERS = ROOT / "audio" / "fillers.py"


def _source() -> str:
    return MAIN.read_text(encoding="utf-8")


def _chat_source() -> str:
    return CHAT.read_text(encoding="utf-8")


def _fillers_source() -> str:
    return FILLERS.read_text(encoding="utf-8")


def test_reading_mode_wake_word_can_pause_processing_task():
    source = _source()
    assert 'interrupt_reason = "reading_pause"' in source
    assert 'MODE == "reading" and kw in WAKE_WORDS' in source
    assert 'cancel_event.set()' in source


def test_reading_pause_interrupt_keeps_reading_mode_and_pauses_page():
    source = _source()
    assert 'reason == "reading_pause"' in source
    assert "_stop_reading_tracking(return_home=False)" in source
    assert 'print("[reading] event=paused reason=wake_interrupt mode=reading"' in source
    assert 'MODE = "normal"' not in source[
        source.index('if MODE == "reading" and reason == "reading_pause"'):
        source.index('if MODE == "reading":', source.index('if MODE == "reading" and reason == "reading_pause"') + 1)
    ]


def test_reading_pause_interrupt_queues_wake_feedback():
    source = _source()

    on_wake_start = source.index("def _on_wake")
    on_wake_end = source.index("def _on_vad", on_wake_start)
    on_wake = source[on_wake_start:on_wake_end]
    assert "pending_wake_reply = True" in on_wake

    handler_start = source.index("def handle_interrupt")
    handler_end = source.index("# ── 主循环", handler_start)
    handler = source[handler_start:handler_end]
    assert "pending_wake_reply" in handler
    assert "_play_wake_reply_async()" in handler


def test_reading_entry_uses_progress_prompt_without_reading_in_filler():
    source = _source()
    branch_start = source.index("if is_reading_entry:")
    branch_end = source.index('conv.messages[0] = {', branch_start)
    branch = source[branch_start:branch_end]

    assert "think_filler()" in branch
    assert branch.index("think_filler()") < branch.index('speaker.feed("正在进入读书模式，请稍候。")')
    assert 'speaker.feed("正在进入读书模式，请稍候。")' in branch
    assert "reading_in_filler()" not in branch


def test_reading_entry_does_not_wait_for_progress_prompt_before_tracking():
    source = _source()
    branch_start = source.index("if is_reading_entry:")
    branch_end = source.index('conv.messages[0] = {', branch_start)
    branch = source[branch_start:branch_end]
    progress_start = branch.index('speaker.feed("正在进入读书模式，请稍候。")')
    tracking_start = branch.index("_start_reading_tracking()")
    progress_to_tracking = branch[progress_start:tracking_start]

    assert "speaker.wait()" not in progress_to_tracking


def test_reading_chat_transition_gives_immediate_think_feedback():
    source = _source()
    start = source.index("if reading_chat_transition:")
    end = source.index("if (\n            person_intent", start)
    branch = source[start:end]

    assert "think_filler()" in branch
    assert branch.index("think_filler()") < branch.index("_stop_reading_tracking(return_home=True)")


def test_reading_chat_transition_does_not_repeat_generic_think_feedback():
    source = _source()
    start = source.index("def process_utterance")
    end = source.index("# ── 保存当前状态", start)
    setup = source[start:end]

    assert "immediate_filler_played = True" in setup
    assert "if not immediate_filler_played and not (" in setup


def test_reading_take_photo_uses_neutral_reading_photo_prompt():
    chat = _chat_source()
    fillers = _fillers_source()

    assert "reading_photo_filler" in chat
    assert "reading_photo_filler" in fillers
    assert "filler.reading_photo.look.01" in fillers
    reading_photo_block = fillers[
        fillers.index('"reading_photo": ['):fillers.index('"reading_in": [')
    ]
    assert "我看一下。" in reading_photo_block
    assert "我们开始读书" not in reading_photo_block
    assert "if is_reading:" in chat
    assert "reading_photo_filler()" in chat


def test_reading_chat_request_exits_reading_before_normal_reply():
    source = _source()
    assert "READING_CHAT_EXIT_REPLY" in source
    assert "is_reading_chat_request" in source
    assert "reading_chat_transition" in source
    assert 'speaker.feed(READING_CHAT_EXIT_REPLY)' in source
    assert "_stop_reading_tracking(return_home=True)" in source


def test_reading_continue_request_stays_in_reading_mode():
    source = _source()
    assert "READING_CONTINUE_KW" in source
    assert "is_reading_continue_request" in source
    assert "not is_reading_continue" in source
    assert 'elif MODE == "reading":' in source
    assert "_start_reading_tracking()" in source


def test_reading_exit_marks_normal_before_blocking_scheduler_stop():
    source = _source()
    start = source.index("# ── 读书模式退出 ──")
    end = source.index("if reading_chat_transition:", start)
    branch = source[start:end]
    assert 'MODE = "normal"' in branch
    assert 'cancel_event.clear()' in branch
    assert 'interrupt_reason = None' in branch
    assert branch.index('MODE = "normal"') < branch.index("_stop_reading_tracking(return_home=True)")


def test_reading_exit_announces_progress_and_completion_around_scheduler_stop():
    source = _source()
    start = source.index("# ── 读书模式退出 ──")
    end = source.index("if reading_chat_transition:", start)
    branch = source[start:end]

    assert 'speaker.feed("正在退出读书模式，请稍候。")' in branch
    assert 'speaker.feed("已退出读书模式。")' in branch
    assert branch.index('speaker.feed("正在退出读书模式，请稍候。")') < branch.index(
        "_stop_reading_tracking(return_home=True)"
    )
    assert branch.index("_stop_reading_tracking(return_home=True)") < branch.index(
        'speaker.feed("已退出读书模式。")'
    )


def test_reading_chat_transition_marks_normal_before_blocking_scheduler_stop():
    source = _source()
    start = source.index("if reading_chat_transition:")
    end = source.index("if (\n            person_intent", start)
    branch = source[start:end]
    assert 'MODE = "normal"' in branch
    assert 'cancel_event.clear()' in branch
    assert 'interrupt_reason = None' in branch
    assert branch.index('MODE = "normal"') < branch.index("_stop_reading_tracking(return_home=True)")


def test_new_utterance_clears_stale_cancel_before_classification():
    source = _source()
    start = source.index("def process_utterance")
    end = source.index("is_story = any", start)
    setup = source[start:end]
    assert "cancel_event.clear()" in setup
    assert "interrupt_reason = None" in setup


def test_cancelled_reading_ask_checks_cancel_before_page_pause():
    source = _source()
    start = source.index("response_text = conv.ask")
    cancel_pos = source.index("if cancel_event.is_set():", start)
    pause_pos = source.index('if MODE == "reading":', start)
    assert cancel_pos < pause_pos


def test_user_exit_then_wake_then_reenter_sequence_simulation():
    wake_words = {"你好小智", "小智小智"}
    interrupt_words = {"停一下", "停一停", "停止", "暂停", "安静", "别说", "先别说"}

    mode = "reading"
    is_processing = True
    cancel_set = False
    interrupt_reason = None

    # User says "退出读书模式": the fixed flow marks normal before scheduler stop blocks.
    mode = "normal"
    cancel_set = False
    interrupt_reason = None

    # While scheduler is still stopping/restoring resources, user says wake word again.
    kw = "你好小智"
    if is_processing:
        if mode == "reading" and kw in wake_words:
            interrupt_reason = "reading_pause"
            cancel_set = True
            decision = "reading_pause"
        elif kw in interrupt_words:
            interrupt_reason = "interrupt"
            cancel_set = True
            decision = "interrupt"
        else:
            decision = "ignored_processing"
    else:
        decision = "wake"

    assert decision == "ignored_processing"
    assert cancel_set is False
    assert interrupt_reason is None

    # Next utterance starts from a clean cancel state.
    is_processing = True
    cancel_set = False
    interrupt_reason = None
    assert mode == "normal"
    assert cancel_set is False
    assert interrupt_reason is None


if __name__ == "__main__":
    test_reading_mode_wake_word_can_pause_processing_task()
    print("test_reading_mode_wake_word_can_pause_processing_task PASS")
    test_reading_pause_interrupt_keeps_reading_mode_and_pauses_page()
    print("test_reading_pause_interrupt_keeps_reading_mode_and_pauses_page PASS")
    test_reading_pause_interrupt_queues_wake_feedback()
    print("test_reading_pause_interrupt_queues_wake_feedback PASS")
    test_reading_entry_uses_progress_prompt_without_reading_in_filler()
    print("test_reading_entry_uses_progress_prompt_without_reading_in_filler PASS")
    test_reading_entry_does_not_wait_for_progress_prompt_before_tracking()
    print("test_reading_entry_does_not_wait_for_progress_prompt_before_tracking PASS")
    test_reading_chat_transition_gives_immediate_think_feedback()
    print("test_reading_chat_transition_gives_immediate_think_feedback PASS")
    test_reading_chat_transition_does_not_repeat_generic_think_feedback()
    print("test_reading_chat_transition_does_not_repeat_generic_think_feedback PASS")
    test_reading_take_photo_uses_neutral_reading_photo_prompt()
    print("test_reading_take_photo_uses_neutral_reading_photo_prompt PASS")
    test_reading_chat_request_exits_reading_before_normal_reply()
    print("test_reading_chat_request_exits_reading_before_normal_reply PASS")
    test_reading_continue_request_stays_in_reading_mode()
    print("test_reading_continue_request_stays_in_reading_mode PASS")
    test_reading_exit_marks_normal_before_blocking_scheduler_stop()
    print("test_reading_exit_marks_normal_before_blocking_scheduler_stop PASS")
    test_reading_exit_announces_progress_and_completion_around_scheduler_stop()
    print("test_reading_exit_announces_progress_and_completion_around_scheduler_stop PASS")
    test_reading_chat_transition_marks_normal_before_blocking_scheduler_stop()
    print("test_reading_chat_transition_marks_normal_before_blocking_scheduler_stop PASS")
    test_new_utterance_clears_stale_cancel_before_classification()
    print("test_new_utterance_clears_stale_cancel_before_classification PASS")
    test_cancelled_reading_ask_checks_cancel_before_page_pause()
    print("test_cancelled_reading_ask_checks_cancel_before_page_pause PASS")
    test_user_exit_then_wake_then_reenter_sequence_simulation()
    print("test_user_exit_then_wake_then_reenter_sequence_simulation PASS")
    print("ALL PASS")
