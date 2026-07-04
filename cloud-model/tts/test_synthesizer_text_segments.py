"""Tests for TTS text normalization and soft splitting."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tts.synthesizer import split_tts_text


def test_flush_tail_gets_terminal_punctuation():
    segments = split_tts_text("明天还要和朋友们玩呢", ensure_terminal_punctuation=True)

    assert segments == ["明天还要和朋友们玩呢。"]


def test_long_flush_text_soft_splits_on_natural_breaks():
    text = "天亮时，阳光暖暖地洒在它身上，它悄悄说：明天还要和朋友们玩呢"

    segments = split_tts_text(text, ensure_terminal_punctuation=True)

    assert segments == [
        "天亮时。",
        "阳光暖暖地洒在它身上。",
        "它悄悄说。",
        "明天还要和朋友们玩呢。",
    ]


def test_sentence_with_hard_punctuation_is_not_changed():
    segments = split_tts_text("你好呀。", ensure_terminal_punctuation=True)

    assert segments == ["你好呀。"]


if __name__ == "__main__":
    test_flush_tail_gets_terminal_punctuation()
    test_long_flush_text_soft_splits_on_natural_breaks()
    test_sentence_with_hard_punctuation_is_not_changed()
    print("ALL PASS")
