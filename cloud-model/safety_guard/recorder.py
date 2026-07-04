"""Persistent safety event recorder."""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict

from .types import SafetyAnalysis, SafetyCandidate


class SafetyEventRecorder:
    def __init__(self, root: Path):
        self.root = root.expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(self, candidate: SafetyCandidate, analysis: SafetyAnalysis) -> Dict:
        day = datetime.now().strftime("%Y-%m-%d")
        rel_dir = Path(day) / candidate.event_id
        event_dir = self.root / rel_dir
        event_dir.mkdir(parents=True, exist_ok=True)

        raw_path = event_dir / "raw.jpg"
        annotated_path = event_dir / "annotated.jpg"
        rknn_path = event_dir / "rknn_status.json"
        vlm_path = event_dir / "vlm_response.json"
        event_path = event_dir / "event.json"

        raw_path.write_bytes(candidate.raw_jpeg)
        annotated_path.write_bytes(candidate.annotated_jpeg)
        rknn_path.write_text(
            json.dumps(candidate.rknn_status, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        vlm_path.write_text(
            json.dumps(analysis.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        event = {
            "event_id": candidate.event_id,
            "created_at": candidate.created_at,
            "candidate_type": candidate.candidate_type,
            "confirmed": bool(analysis.danger),
            "severity": analysis.severity,
            "risk_type": analysis.risk_type,
            "summary": analysis.summary,
            "tts": analysis.tts,
            "paths": {
                "raw": str(rel_dir / "raw.jpg"),
                "annotated": str(rel_dir / "annotated.jpg"),
                "event": str(rel_dir / "event.json"),
                "rknn": str(rel_dir / "rknn_status.json"),
                "vlm": str(rel_dir / "vlm_response.json"),
            },
            "rknn_counts": candidate.rknn_status.get("counts", {}),
            "analysis": analysis.to_dict(),
        }
        event_path.write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding="utf-8")

        index_item = {
            "event_id": event["event_id"],
            "created_at": event["created_at"],
            "candidate_type": event["candidate_type"],
            "confirmed": event["confirmed"],
            "severity": event["severity"],
            "risk_type": event["risk_type"],
            "summary": event["summary"],
            "paths": event["paths"],
        }
        with self._lock:
            with (self.root / "index.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(index_item, ensure_ascii=False) + "\n")
        return event
