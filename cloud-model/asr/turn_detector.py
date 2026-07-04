"""Wake-session turn collection for ASR.

This module is intentionally independent from sherpa_onnx so the timing logic
can be tested without loading models. The detector starts fresh when ASR enters
AWAKE mode and keeps only a small rolling pre-roll before VAD becomes active.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List


@dataclass
class TurnDecision:
    kind: str
    reason: str = ""
    samples: List[float] | None = None


class AwakeTurnDetector:
    """Collect one user turn from wake-session audio and VAD state."""

    def __init__(
        self,
        sample_rate: int,
        preroll_ms: int,
        min_speech_ms: int,
        trailing_silence_ms: int,
        no_speech_timeout_ms: int,
        max_turn_ms: int,
    ):
        self.sample_rate = int(sample_rate)
        self.preroll_samples = self._ms_to_samples(preroll_ms)
        self.min_speech_samples = self._ms_to_samples(min_speech_ms)
        self.trailing_silence_samples = self._ms_to_samples(trailing_silence_ms)
        self.no_speech_timeout_samples = self._ms_to_samples(no_speech_timeout_ms)
        self.max_turn_samples = self._ms_to_samples(max_turn_ms)
        self.reset()

    def _ms_to_samples(self, ms: int) -> int:
        return max(1, int(self.sample_rate * int(ms) / 1000))

    def reset(self) -> None:
        self._state = "waiting"
        self._preroll_chunks: List[List[float]] = []
        self._preroll_count = 0
        self._turn_chunks: List[List[float]] = []
        self._turn_count = 0
        self._speech_count = 0
        self._trailing_count = 0
        self._no_speech_count = 0

    def observe(self, samples: Iterable[float], speaking: bool) -> TurnDecision:
        chunk = list(samples)
        if not chunk:
            return TurnDecision("collecting")

        if self._state == "waiting":
            self._no_speech_count += len(chunk)
            if speaking:
                self._state = "speaking"
                self._turn_chunks = [list(c) for c in self._preroll_chunks]
                self._turn_count = sum(len(c) for c in self._turn_chunks)
                self._append_turn(chunk)
                self._speech_count = len(chunk)
                self._trailing_count = 0
                return TurnDecision("collecting")
            self._append_preroll(chunk)
            if self._no_speech_count >= self.no_speech_timeout_samples:
                self.reset()
                return TurnDecision("timeout", "no_speech")
            return TurnDecision("collecting")

        self._append_turn(chunk)
        if speaking:
            self._speech_count += len(chunk)
            self._trailing_count = 0
        else:
            self._trailing_count += len(chunk)

        if self._turn_count >= self.max_turn_samples and self._speech_count >= self.min_speech_samples:
            return self._finish("ready", "max_turn")

        if self._trailing_count >= self.trailing_silence_samples:
            if self._speech_count >= self.min_speech_samples:
                return self._finish("ready", "trailing_silence")
            self.reset()
            return TurnDecision("discarded", "speech_too_short")

        return TurnDecision("collecting")

    def _append_preroll(self, chunk: List[float]) -> None:
        self._preroll_chunks.append(chunk)
        self._preroll_count += len(chunk)
        while self._preroll_count > self.preroll_samples and self._preroll_chunks:
            removed = self._preroll_chunks.pop(0)
            self._preroll_count -= len(removed)

    def _append_turn(self, chunk: List[float]) -> None:
        self._turn_chunks.append(chunk)
        self._turn_count += len(chunk)

    def _finish(self, kind: str, reason: str) -> TurnDecision:
        samples: List[float] = []
        for chunk in self._turn_chunks:
            samples.extend(chunk)
        self.reset()
        return TurnDecision(kind, reason, samples)
