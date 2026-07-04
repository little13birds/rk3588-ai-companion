"""Safety TTS announcement coordination."""
from __future__ import annotations

import threading
import time
from typing import Optional

from .phrases import SafetyPhraseChoice, select_safety_phrase
from .types import SEVERITY_RANK, SafetyAnalysis, SafetyCandidate


class SafetyAnnouncer:
    def __init__(
        self,
        speaker,
        cancel_event=None,
        min_severity: str = "medium",
        announce_cooldown_sec: float = 20.0,
    ):
        self.speaker = speaker
        self.cancel_event = cancel_event
        self.min_rank = SEVERITY_RANK.get(min_severity, 2)
        self.announce_cooldown_sec = max(0.0, float(announce_cooldown_sec))
        self._last_announce = {}
        self._lock = threading.Lock()

    def should_announce(self, analysis: SafetyAnalysis) -> bool:
        if not self.speaker:
            return False
        if not analysis.danger:
            return False
        return analysis.severity_rank >= self.min_rank

    def prepare_phrase(
        self,
        analysis: SafetyAnalysis,
        candidate: Optional[SafetyCandidate] = None,
    ) -> Optional[SafetyPhraseChoice]:
        if not analysis.danger:
            return None
        choice = select_safety_phrase(analysis, candidate)
        analysis.tts = choice.text
        analysis.tts_phrase_id = choice.phrase_id
        analysis.tts_source = "fixed_phrase_cache"
        return choice

    def announce(
        self,
        analysis: SafetyAnalysis,
        candidate: Optional[SafetyCandidate] = None,
        phrase_choice: Optional[SafetyPhraseChoice] = None,
    ) -> bool:
        if not self.should_announce(analysis):
            return False
        choice = phrase_choice or self.prepare_phrase(analysis, candidate)
        text = (choice.text if choice else analysis.tts).strip()
        if not text:
            return False

        with self._lock:
            key = choice.repeat_key if choice else analysis.risk_type or "safety"
            now = time.monotonic()
            last = self._last_announce.get(key, -9999.0)
            if now - last < self.announce_cooldown_sec:
                print(
                    "[safety] event=announce_suppressed component=announcer key=%s elapsed_sec=%.1f"
                    % (key, now - last),
                    flush=True,
                )
                return False
            self._last_announce[key] = now

            if analysis.severity in {"high", "critical"} and self.cancel_event is not None:
                self.cancel_event.set()
                try:
                    self.speaker.cancel()
                except Exception:
                    pass
            try:
                self.speaker.reset()
                if choice and hasattr(self.speaker, "queue_phrase"):
                    self.speaker.queue_phrase(choice.phrase_id, text, voice=choice.voice)
                else:
                    self.speaker.feed(text)
                    self.speaker.flush()
                self.speaker.wait()
                return True
            except Exception as exc:
                print(f"[safety] event=announce_failed component=announcer error={exc}", flush=True)
                return False
