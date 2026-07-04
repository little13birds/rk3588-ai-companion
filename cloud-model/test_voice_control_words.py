"""Static checks for shared voice control words."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent
MAIN_SOURCE = (ROOT / "main.py").read_text(encoding="utf-8")
VOICE_DEBUG_SOURCE = (ROOT / "voice_dialog_debug.py").read_text(encoding="utf-8")
KEYWORDS = (ROOT / "asr" / "kws_keywords.txt").read_text(encoding="utf-8")


def test_main_interrupt_words_cover_kws_stop_variants():
    for word in ("停", "停一下", "停一停", "暂停", "安静", "停止", "别说", "先别说"):
        assert f'"{word}"' in MAIN_SOURCE


def test_voice_debug_and_kws_keywords_share_stop_variants():
    for word in ("停", "暂停", "安静", "停止"):
        assert f'"{word}"' in VOICE_DEBUG_SOURCE
        assert f"@{word}" in KEYWORDS


if __name__ == "__main__":
    test_main_interrupt_words_cover_kws_stop_variants()
    print("test_main_interrupt_words_cover_kws_stop_variants PASS")
    test_voice_debug_and_kws_keywords_share_stop_variants()
    print("test_voice_debug_and_kws_keywords_share_stop_variants PASS")
    print("ALL PASS")
