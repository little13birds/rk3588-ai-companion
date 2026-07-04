"""Unit tests for wake-session ASR turn collection."""

from __future__ import annotations

from asr.turn_detector import AwakeTurnDetector


def _chunk(value: int, samples: int = 100):
    return [float(value)] * samples


def _values(samples):
    result = []
    last = object()
    for sample in samples:
        value = int(sample)
        if value != last:
            result.append(value)
            last = value
    return result


def test_preserves_awake_preroll_when_vad_starts_late():
    detector = AwakeTurnDetector(
        sample_rate=1000,
        preroll_ms=300,
        min_speech_ms=200,
        trailing_silence_ms=200,
        no_speech_timeout_ms=2000,
        max_turn_ms=5000,
    )

    for value in range(5):
        assert detector.observe(_chunk(value), speaking=False).kind == "collecting"

    assert detector.observe(_chunk(5), speaking=True).kind == "collecting"
    assert detector.observe(_chunk(6), speaking=True).kind == "collecting"
    assert detector.observe(_chunk(7), speaking=False).kind == "collecting"
    decision = detector.observe(_chunk(8), speaking=False)

    assert decision.kind == "ready"
    assert decision.reason == "trailing_silence"
    assert _values(decision.samples) == [2, 3, 4, 5, 6, 7, 8]


def test_times_out_if_no_speech_after_wake():
    detector = AwakeTurnDetector(
        sample_rate=1000,
        preroll_ms=300,
        min_speech_ms=200,
        trailing_silence_ms=200,
        no_speech_timeout_ms=500,
        max_turn_ms=5000,
    )

    for value in range(4):
        assert detector.observe(_chunk(value), speaking=False).kind == "collecting"
    decision = detector.observe(_chunk(4), speaking=False)

    assert decision.kind == "timeout"
    assert decision.reason == "no_speech"


def test_discards_too_short_speech_spike():
    detector = AwakeTurnDetector(
        sample_rate=1000,
        preroll_ms=300,
        min_speech_ms=200,
        trailing_silence_ms=200,
        no_speech_timeout_ms=2000,
        max_turn_ms=5000,
    )

    assert detector.observe(_chunk(0), speaking=False).kind == "collecting"
    assert detector.observe(_chunk(1), speaking=True).kind == "collecting"
    assert detector.observe(_chunk(2), speaking=False).kind == "collecting"
    decision = detector.observe(_chunk(3), speaking=False)

    assert decision.kind == "discarded"
    assert decision.reason == "speech_too_short"
    assert detector.observe(_chunk(4), speaking=False).kind == "collecting"


if __name__ == "__main__":
    test_preserves_awake_preroll_when_vad_starts_late()
    print("test_preserves_awake_preroll_when_vad_starts_late PASS")
    test_times_out_if_no_speech_after_wake()
    print("test_times_out_if_no_speech_after_wake PASS")
    test_discards_too_short_speech_spike()
    print("test_discards_too_short_speech_spike PASS")
    print("ALL PASS")
