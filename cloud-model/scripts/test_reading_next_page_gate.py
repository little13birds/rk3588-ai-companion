"""Regression tests for reading-mode next-page prompting."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reading_mode import classify_reading_turn, should_prompt_next_page


FAILURE_REPLY = (
    "可能是摄像头没拍到或者光线太暗啦～。"
    "请把书放正一点，离镜头近一点，再试一次好吗？"
    "需要小智再帮您拍一次吗？"
)


def test_failed_capture_never_prompts_next_page():
    outcome = classify_reading_turn("小兔子回到了家。", {"capture_ok": False})
    assert outcome["successful"] is False
    assert outcome["prompt_next_page"] is False


def test_retry_language_does_not_prompt_next_page_even_with_image():
    outcome = classify_reading_turn(FAILURE_REPLY, {"capture_ok": True})
    assert outcome["successful"] is False
    assert outcome["prompt_next_page"] is False
    assert should_prompt_next_page(FAILURE_REPLY) is False


def test_successful_reading_without_model_question_prompts_next_page():
    outcome = classify_reading_turn("小兔子回到了家。", {"capture_ok": True})
    assert outcome["successful"] is True
    assert outcome["prompt_next_page"] is True


def test_model_next_page_question_is_success_without_duplicate_prompt():
    outcome = classify_reading_turn(
        "小兔子回到了家。需要继续读下一页吗？",
        {"capture_ok": True},
    )
    assert outcome["successful"] is True
    assert outcome["prompt_next_page"] is False
