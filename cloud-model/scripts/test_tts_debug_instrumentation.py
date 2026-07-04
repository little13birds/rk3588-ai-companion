"""Static checks for LLM/TTS debug instrumentation."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHAT = ROOT / "chat.py"
REALTIME_TTS = ROOT / "tts" / "realtime_tts.py"


def test_chat_logs_stream_deltas_and_finish_reason_to_file_only_logger():
    text = CHAT.read_text(encoding="utf-8")
    assert "from utils.tts_debug_log import log_tts_event, text_tail" in text
    assert '"llm_delta"' in text
    assert '"llm_stream_done"' in text
    assert "finish_reason" in text


def test_realtime_tts_logs_queue_synth_audio_play_and_flush_boundaries():
    text = REALTIME_TTS.read_text(encoding="utf-8")
    assert "from utils.tts_debug_log import log_tts_event, text_tail" in text
    for token in [
        '"tts_queue_sentence"',
        '"tts_flush_buffer"',
        '"tts_synth_done"',
        '"tts_audio_enqueue"',
        '"tts_play_pcm_done"',
        '"tts_play_wav_done"',
        '"tts_cancel"',
    ]:
        assert token in text


def test_realtime_tts_console_logs_do_not_truncate_to_25_chars():
    text = REALTIME_TTS.read_text(encoding="utf-8")
    assert "text[:25]" not in text
    assert "_console_text" in text


def test_realtime_tts_retries_empty_audio_response_before_playback():
    text = REALTIME_TTS.read_text(encoding="utf-8")
    assert "TTS 返回空音频" in text
    assert "if not pcm:" in text


def test_realtime_tts_normalizes_terminal_punctuation_for_all_queue_sources():
    text = REALTIME_TTS.read_text(encoding="utf-8")
    assert "ensure_terminal_punctuation=True" in text
    assert "ensure_terminal_punctuation=(source == \"flush\")" not in text


if __name__ == "__main__":
    test_chat_logs_stream_deltas_and_finish_reason_to_file_only_logger()
    test_realtime_tts_logs_queue_synth_audio_play_and_flush_boundaries()
    test_realtime_tts_console_logs_do_not_truncate_to_25_chars()
    test_realtime_tts_retries_empty_audio_response_before_playback()
    test_realtime_tts_normalizes_terminal_punctuation_for_all_queue_sources()
    print("ALL PASS")
