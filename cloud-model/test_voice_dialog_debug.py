"""Static checks for voice dialog debug process behavior."""

from __future__ import annotations

from pathlib import Path


SOURCE = Path(__file__).with_name("voice_dialog_debug.py").read_text(encoding="utf-8")


def test_keyboard_interrupt_exit_uses_safe_print():
    assert "def _safe_print" in SOURCE
    assert '_safe_print("\\n[voice_debug] event=exit")' in SOURCE


def test_stop_words_are_handled_before_llm():
    assert 'STOP_WORDS = {' in SOURCE
    for word in ("停", "停止", "暂停", "安静", "别说", "先别说"):
        assert f'"{word}"' in SOURCE
    assert "if text in STOP_WORDS:" in SOURCE
    assert "event=stop_listening" in SOURCE


if __name__ == "__main__":
    test_keyboard_interrupt_exit_uses_safe_print()
    print("test_keyboard_interrupt_exit_uses_safe_print PASS")
    test_stop_words_are_handled_before_llm()
    print("test_stop_words_are_handled_before_llm PASS")
    print("ALL PASS")
