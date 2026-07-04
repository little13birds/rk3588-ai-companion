"""VLM-based safety risk review."""
from __future__ import annotations

import base64
import json
import re

from openai import OpenAI

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from .config import SafetyGuardConfig
from .prompts import SAFETY_ANALYSIS_SYSTEM_PROMPT, build_safety_user_text
from .types import SafetyAnalysis, SafetyCandidate


def _extract_json(text: str) -> dict:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start:end + 1]
    return json.loads(cleaned)


class SafetyRiskAnalyzer:
    def __init__(self, config: SafetyGuardConfig):
        self.config = config
        self._client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

    def analyze(self, candidate: SafetyCandidate) -> SafetyAnalysis:
        image_b64 = base64.b64encode(candidate.raw_jpeg).decode("ascii")
        user_text = build_safety_user_text(candidate.candidate_type, candidate.rknn_status)
        try:
            response = self._client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": SAFETY_ANALYSIS_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": "data:image/jpeg;base64," + image_b64},
                            },
                            {"type": "text", "text": user_text},
                        ],
                    },
                ],
                stream=False,
                max_tokens=220,
                timeout=self.config.analyzer_timeout_sec,
            )
            raw = response.choices[0].message.content or ""
            data = _extract_json(raw)
            return SafetyAnalysis(
                danger=bool(data.get("danger", False)),
                risk_type=str(data.get("risk_type", "unknown")),
                severity=str(data.get("severity", "low")),
                summary=str(data.get("summary", "")),
                tts=str(data.get("tts", "")),
                evidence=list(data.get("evidence", [])) if isinstance(data.get("evidence", []), list) else [],
                recommended_action=str(data.get("recommended_action", "")),
                raw_response=raw,
            )
        except Exception as exc:
            return SafetyAnalysis(
                danger=False,
                risk_type="unknown",
                severity="low",
                summary="安全复核失败，已按疑似事件记录。",
                tts="",
                raw_response="",
                parse_error=str(exc),
            )
