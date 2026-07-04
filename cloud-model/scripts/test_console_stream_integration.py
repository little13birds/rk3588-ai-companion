"""Static checks for console stream/log coordination."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHAT = ROOT / "llm" / "chat.py"
MAIN = ROOT / "main.py"
REALTIME_TTS = ROOT / "tts" / "realtime_tts.py"


def test_chat_stream_uses_console_stream_helpers():
    text = CHAT.read_text(encoding="utf-8")
    assert "from utils.console_io import console_print, console_stream, console_write" in text
    assert "with console_stream():" in text
    assert "console_write(emitted)" in text
    assert "print(delta.content" not in text
    assert "print(emitted" not in text


def test_processing_progress_is_deferred_during_stream_output():
    text = MAIN.read_text(encoding="utf-8")
    assert "from utils.console_io import console_print" in text
    assert "defer_during_stream=True" in text
    assert '                print("[处理中]' not in text


def test_realtime_tts_background_logs_defer_during_stream_output():
    text = REALTIME_TTS.read_text(encoding="utf-8")
    assert "from utils.console_io import console_print" in text
    assert "defer_during_stream=True" in text
    assert '                print(\n                    "[tts.synth] event=sentence_done' not in text


if __name__ == "__main__":
    test_chat_stream_uses_console_stream_helpers()
    test_processing_progress_is_deferred_during_stream_output()
    test_realtime_tts_background_logs_defer_during_stream_output()
    print("ALL PASS")
