"""Regression tests for short wake reply phrases.

Run from repo root:
    python3 -m scripts.test_wake_phrases
"""
from __future__ import annotations

import re
from pathlib import Path

from audio import fillers


CLAUSE_PUNCTUATION = set("，、；;,.")
SENTENCE_PUNCTUATION_RE = re.compile(r"[。！？!?\\s]+")


def _spoken_len(text: str) -> int:
    return len(SENTENCE_PUNCTUATION_RE.sub("", text))


def test_wake_phrases_are_short_single_clause_prompts():
    wake_phrases = fillers._PHRASES["wake"]
    texts = [text for _phrase_id, text in wake_phrases]
    assert "我在。" in texts
    assert "请说。" in texts
    assert len(texts) >= 5
    assert fillers._FALLBACKS["wake"] == "wake_"
    for text in texts:
        assert not any(mark in text for mark in CLAUSE_PUNCTUATION), (
            f"wake phrase should be a short single clause, got: {text}"
        )
        assert _spoken_len(text) <= 4, (
            f"wake phrase should be brief enough not to cover user speech, got: {text}"
        )


class _FakeSpeaker:
    def __init__(self):
        self.phrases = []
        self.wavs = []

    def queue_phrase(self, *args, **kwargs):
        self.phrases.append((args, kwargs))
        return True

    def queue_wav(self, path):
        self.wavs.append(path)


def test_wake_reply_uses_local_wav_without_realtime_generation():
    fake = _FakeSpeaker()
    previous = fillers._speaker
    try:
        fillers.set_speaker(fake)
        fillers.wake_reply()
    finally:
        fillers.set_speaker(previous)

    assert fake.phrases == [], (
        "Wake reply must not call queue_phrase because an uncached phrase can "
        "block on realtime TTS and play after the assistant has already slept."
    )
    assert len(fake.wavs) == 1
    assert Path(fake.wavs[0]).name.startswith("wake_")


if __name__ == "__main__":
    test_wake_phrases_are_short_single_clause_prompts()
    print("test_wake_phrases_are_short_single_clause_prompts PASS")
    test_wake_reply_uses_local_wav_without_realtime_generation()
    print("test_wake_reply_uses_local_wav_without_realtime_generation PASS")
    print("ALL PASS")
