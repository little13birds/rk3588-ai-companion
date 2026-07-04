"""Tests for file-only TTS/LLM debug logging."""

import json
import tempfile
from pathlib import Path

from utils.tts_debug_log import log_tts_event, text_tail


def test_log_tts_event_writes_jsonl_to_requested_file():
    root = Path(tempfile.mkdtemp())
    path = root / "tts_debug.jsonl"

    ok = log_tts_event(
        "unit_event",
        log_path=str(path),
        sentence_id=3,
        text="掌心",
    )

    assert ok is True
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    item = json.loads(lines[0])
    assert item["event"] == "unit_event"
    assert item["sentence_id"] == 3
    assert item["text"] == "掌心"
    assert "ts" in item


def test_text_tail_keeps_last_characters_and_length():
    result = text_tail("春天的掌心", limit=2)

    assert result == {"len": 5, "tail": "掌心"}


if __name__ == "__main__":
    test_log_tts_event_writes_jsonl_to_requested_file()
    test_text_tail_keeps_last_characters_and_length()
    print("ALL PASS")
