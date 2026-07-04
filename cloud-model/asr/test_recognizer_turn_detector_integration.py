"""Static integration checks for ASRProcessor turn collection wiring.

These checks avoid importing sherpa_onnx so they can run on development hosts
without model/runtime dependencies.
"""

from __future__ import annotations

from pathlib import Path


SOURCE = Path(__file__).with_name("recognizer.py").read_text(encoding="utf-8")
KEYWORDS = Path(__file__).with_name("kws_keywords.txt").read_text(encoding="utf-8")


def test_recognizer_uses_awake_turn_detector():
    assert "from asr.turn_detector import AwakeTurnDetector" in SOURCE
    assert "self._turn_detector = AwakeTurnDetector" in SOURCE
    assert "decision = self._turn_detector.observe(samples, speaking)" in SOURCE


def test_recognizer_decodes_collected_turn_not_vad_front_segment():
    assert "def _decode_turn_samples" in SOURCE
    assert "_decode_turn_samples(decision.samples" in SOURCE
    assert "seg = self.vad.front" not in SOURCE
    assert "stream.accept_waveform(SAMPLE_RATE, seg.samples)" not in SOURCE


def test_recognizer_prefers_repo_kws_keywords():
    assert "_REPO_KWS_KEYWORDS" in SOURCE
    assert "ASR_KWS_KEYWORDS" in SOURCE
    assert "kws_keywords.txt" in SOURCE
    assert "@停止" in KEYWORDS


def test_kws_keywords_exclude_single_character_stop():
    labels = {
        line.rsplit("@", 1)[1].strip()
        for line in KEYWORDS.splitlines()
        if "@" in line
    }
    assert "停" not in labels
    for phrase in {"停一下", "停一停", "停止", "暂停"}:
        assert phrase in labels


if __name__ == "__main__":
    test_recognizer_uses_awake_turn_detector()
    print("test_recognizer_uses_awake_turn_detector PASS")
    test_recognizer_decodes_collected_turn_not_vad_front_segment()
    print("test_recognizer_decodes_collected_turn_not_vad_front_segment PASS")
    test_recognizer_prefers_repo_kws_keywords()
    print("test_recognizer_prefers_repo_kws_keywords PASS")
    test_kws_keywords_exclude_single_character_stop()
    print("test_kws_keywords_exclude_single_character_stop PASS")
    print("ALL PASS")
