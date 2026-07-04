"""Reading-mode stream filtering tests.

Run from ~/cloud-model with: python3 -m llm.test_reading_stream_filter
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.modules.setdefault(
    "openai",
    SimpleNamespace(
        OpenAI=lambda **_kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=None)
        )
    ),
)

from llm import chat
from reading_mode import ReadingStreamFilter


class FakeSpeaker:
    def __init__(self):
        self.parts = []

    def feed(self, text):
        self.parts.append(text)


class FakeCompletions:
    def create(self, **kwargs):
        assert kwargs["stream"] is True
        return [
            _chunk("我是小智，正在读书模式下为你"),
            _chunk("朗读。请稍等。\n\n敬畏的心自己  \n"),
            _chunk("学我敢承认错误\n\n（注：以上为照片中可见文字，按原顺序逐字朗读，未作任何增减或解释）"),
        ]


def _chunk(content):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(delta=SimpleNamespace(content=content))
        ],
        usage=None,
    )


def test_reading_stream_drops_assistant_chatter_and_notes():
    speaker = FakeSpeaker()
    original_client = chat.client
    chat.client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    conv = chat.Conversation(
        system_prompt="你是小智，一个能帮用户读书的OCR朗读器。你已进入持续读书模式。",
        max_tokens=80,
        speaker=speaker,
        tools=None,
    )
    conv.messages.append({"role": "user", "content": "读书"})

    try:
        result = conv._call_api()
    finally:
        chat.client = original_client

    expected = "敬畏的心自己  \n学我敢承认错误\n"
    assert result["text"] == expected
    assert "".join(speaker.parts) == expected
    assert "我是小智" not in result["text"]
    assert "请稍等" not in result["text"]
    assert "注：" not in result["text"]
    print("test_reading_stream_drops_assistant_chatter_and_notes PASS")


def test_reading_tool_instruction_forbids_chatter_and_notes():
    source = Path("llm/chat.py").read_text(encoding="utf-8")

    assert "不要输出自我介绍" in source
    assert "不要说正在读书模式" in source
    assert "不要添加注释" in source
    print("test_reading_tool_instruction_forbids_chatter_and_notes PASS")


def test_reading_stream_emits_complete_sentences_without_newlines():
    stream_filter = ReadingStreamFilter()

    assert stream_filter.feed("炎热的夏天到了，") == ""
    assert stream_filter.feed("小猫安安穿着泳衣，") == ""
    assert stream_filter.feed("戴着小河里游泳。") == (
        "炎热的夏天到了，小猫安安穿着泳衣，戴着小河里游泳。"
    )
    assert stream_filter.feed("需要继续读下一页吗？") == "需要继续读下一页吗？"
    assert stream_filter.flush() == ""
    print("test_reading_stream_emits_complete_sentences_without_newlines PASS")


if __name__ == "__main__":
    test_reading_stream_drops_assistant_chatter_and_notes()
    test_reading_tool_instruction_forbids_chatter_and_notes()
    test_reading_stream_emits_complete_sentences_without_newlines()
    print("ALL PASS")
