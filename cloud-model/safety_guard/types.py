"""Typed records used by the safety guard pipeline."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List


SEVERITY_RANK = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def make_event_id(prefix: str) -> str:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"{stamp}_{prefix}_{uuid.uuid4().hex[:8]}"


@dataclass
class SafetyCandidate:
    event_id: str
    candidate_type: str
    created_at: str
    monotonic_s: float
    raw_jpeg: bytes
    annotated_jpeg: bytes
    rknn_status: Dict[str, Any]

    @classmethod
    def create(
        cls,
        candidate_type: str,
        raw_jpeg: bytes,
        annotated_jpeg: bytes,
        rknn_status: Dict[str, Any],
    ) -> "SafetyCandidate":
        return cls(
            event_id=make_event_id(candidate_type),
            candidate_type=candidate_type,
            created_at=now_iso(),
            monotonic_s=time.monotonic(),
            raw_jpeg=raw_jpeg,
            annotated_jpeg=annotated_jpeg,
            rknn_status=rknn_status,
        )


@dataclass
class SafetyAnalysis:
    danger: bool = False
    risk_type: str = "unknown"
    severity: str = "low"
    summary: str = ""
    tts: str = ""
    evidence: List[str] = field(default_factory=list)
    recommended_action: str = ""
    raw_response: str = ""
    parse_error: str = ""
    tts_phrase_id: str = ""
    tts_source: str = ""

    @property
    def severity_rank(self) -> int:
        return SEVERITY_RANK.get(self.severity, 1)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "danger": self.danger,
            "risk_type": self.risk_type,
            "severity": self.severity,
            "summary": self.summary,
            "tts": self.tts,
            "evidence": self.evidence,
            "recommended_action": self.recommended_action,
            "raw_response": self.raw_response,
            "parse_error": self.parse_error,
            "tts_phrase_id": self.tts_phrase_id,
            "tts_source": self.tts_source,
        }
