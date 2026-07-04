"""Reading-mode next-page prompt tests.

Run from ~/cloud-model with: python3 -m test_reading_mode
"""
from pathlib import Path
import unittest

from reading_mode import should_prompt_next_page


class ShouldPromptNextPageTests(unittest.TestCase):
    def test_successful_ocr_requests_next_page_prompt(self):
        self.assertTrue(
            should_prompt_next_page("春天来了，小草从泥土里探出头来。")
        )

    def test_empty_response_does_not_request_prompt(self):
        self.assertFalse(should_prompt_next_page(""))
        self.assertFalse(should_prompt_next_page(None))

    def test_retry_question_does_not_request_next_page_prompt(self):
        self.assertFalse(
            should_prompt_next_page(
                "照片中的文字太模糊了，请调整角度。需要再试一次吗？"
            )
        )

    def test_capture_failure_does_not_request_next_page_prompt(self):
        self.assertFalse(
            should_prompt_next_page("摄像头拍照失败，请稍后重试。")
        )

    def test_unclear_text_does_not_request_next_page_prompt(self):
        self.assertFalse(
            should_prompt_next_page("这张照片反光严重，文字看不清。")
        )

    def test_existing_next_page_question_is_not_repeated(self):
        self.assertFalse(
            should_prompt_next_page(
                "春天来了。需要继续读下一页吗？"
            )
        )


class MainReadingFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path("main.py").read_text(encoding="utf-8")

    def test_main_uses_returned_response_for_prompt_decision(self):
        self.assertIn(
            "response_text = conv.ask(text, cancel_event=cancel_event)",
            self.source,
        )
        self.assertIn(
            "should_prompt_next_page(response_text)",
            self.source,
        )

    def test_main_queues_and_waits_for_fixed_prompt(self):
        self.assertIn("reading_continue_filler()", self.source)
        prompt_pos = self.source.index("reading_continue_filler()")
        wait_pos = self.source.index("speaker.wait()", prompt_pos)
        self.assertGreater(wait_pos, prompt_pos)

    def test_reading_system_prompt_matches_saved_baseline(self):
        self.assertIn(
            '朗读完成后，必须在最后一句说\\"需要继续读下一页吗？\\"，不可省略。',
            self.source,
        )
        self.assertIn("规则：不寒暄不啰嗦，直接做事。但翻页询问必须说。", self.source)
        self.assertNotIn("翻页询问由系统播放", self.source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
