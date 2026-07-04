"""Static regression checks for reading-mode fixed phrase timing."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
FILLERS = ROOT / "audio" / "fillers.py"
CHAT = ROOT / "llm" / "chat.py"


def _main() -> str:
    return MAIN.read_text(encoding="utf-8")


def _fillers() -> str:
    return FILLERS.read_text(encoding="utf-8")


def _chat() -> str:
    return CHAT.read_text(encoding="utf-8")


def test_reading_photo_phrase_is_neutral_not_start_reading():
    fillers = _fillers()
    assert "filler.reading_photo.look.01" in fillers
    assert '("filler.reading_photo.look.01", "我看一下。")' in fillers
    reading_photo_block = fillers[
        fillers.index('"reading_photo": ['):fillers.index('"reading_in": [')
    ]
    assert "我们开始读书" not in reading_photo_block


def test_reading_continue_and_retry_have_distinct_confirmations():
    fillers = _fillers()
    assert "reading_next_page" in fillers
    assert "reading_retry" in fillers
    assert "好的，继续读下一页。" in fillers
    assert "好的，我再试一次。" in fillers


def test_main_selects_continue_phrase_from_previous_reading_result():
    source = _main()
    assert "last_reading_success = None" in source
    assert "last_reading_success" in source[source.index("def process_utterance"):source.index("# ── 完成处理")]
    assert "reading_next_page_filler()" in source
    assert "reading_retry_filler()" in source

    reading_branch = source[
        source.index("elif MODE == \"reading\":"):source.index("elif is_story:", source.index("elif MODE == \"reading\":"))
    ]
    assert "if is_reading_continue:" in reading_branch
    assert "last_reading_success is False" in reading_branch
    assert "reading_retry_filler()" in reading_branch
    assert "reading_next_page_filler()" in reading_branch
    assert reading_branch.index("reading_next_page_filler()") < reading_branch.index("_start_reading_tracking()")


def test_reading_result_updates_continue_phrase_state():
    source = _main()
    completion = source[
        source.index("# ── 完成处理 ──"):source.index("if MODE == \"story\":", source.index("# ── 完成处理 ──"))
    ]
    assert "last_reading_success = reading_success" in completion


def test_reading_take_photo_keeps_neutral_prompt_in_tool_layer():
    chat = _chat()
    assert "reading_photo_filler()" in chat
    assert "我们开始读书" not in chat


if __name__ == "__main__":
    test_reading_photo_phrase_is_neutral_not_start_reading()
    print("test_reading_photo_phrase_is_neutral_not_start_reading PASS")
    test_reading_continue_and_retry_have_distinct_confirmations()
    print("test_reading_continue_and_retry_have_distinct_confirmations PASS")
    test_main_selects_continue_phrase_from_previous_reading_result()
    print("test_main_selects_continue_phrase_from_previous_reading_result PASS")
    test_reading_result_updates_continue_phrase_state()
    print("test_reading_result_updates_continue_phrase_state PASS")
    test_reading_take_photo_keeps_neutral_prompt_in_tool_layer()
    print("test_reading_take_photo_keeps_neutral_prompt_in_tool_layer PASS")
    print("ALL PASS")
