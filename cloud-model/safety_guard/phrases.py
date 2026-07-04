"""Fixed safety phrases selected from VLM-confirmed risk metadata."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .types import SafetyAnalysis, SafetyCandidate


@dataclass(frozen=True)
class SafetyPhraseChoice:
    phrase_id: str
    text: str
    voice: str = "Cherry"
    repeat_key: str = "safety"


_FALL_PHRASES: List[Tuple[str, str]] = [
    ("safety.fall.01", "检测到你可能摔倒了，别害怕，请先不要乱动，等大人来帮忙。"),
    ("safety.fall.02", "你好像摔倒了，先不要急着站起来，如果哪里疼，就保持不动。"),
    ("safety.fall.03", "没关系，先慢慢呼吸，不要着急，我会提醒大人来看看。"),
    ("safety.fall.04", "请先保护好头和手，不要乱动，等待大人帮助。"),
    ("safety.fall.05", "你可能跌倒了，请先保持原位，大人会来帮你。"),
]

_SCISSORS_PHRASES: List[Tuple[str, str]] = [
    ("safety.sharp.scissors.01", "小心剪刀，请先慢慢放下，等大人来帮你。"),
    ("safety.sharp.scissors.02", "剪刀可能会伤到手，请不要拿着玩，先放到桌子上。"),
    ("safety.sharp.scissors.03", "请把剪刀放下，手离开刀口位置。"),
]

_KNIFE_PHRASES: List[Tuple[str, str]] = [
    ("safety.sharp.knife.01", "小心刀具，请马上放下，等大人来处理。"),
    ("safety.sharp.knife.02", "刀具很锋利，请不要继续拿着，慢慢把手移开。"),
    ("safety.sharp.knife.03", "请不要挥动刀具，先放下，等待大人帮助。"),
]

_FORK_PHRASES: List[Tuple[str, str]] = [
    ("safety.sharp.fork.01", "小心叉子，请先放下，不要对着自己或别人。"),
    ("safety.sharp.fork.02", "叉子可能会扎到手，请慢慢放到桌子上。"),
]

_SHARP_GENERIC_PHRASES: List[Tuple[str, str]] = [
    ("safety.sharp.generic.01", "这个物品可能不安全，请先把手移开。"),
    ("safety.sharp.generic.02", "请先停一下，把尖锐物品放下，等大人来看看。"),
    ("safety.sharp.generic.03", "小心尖锐物品，请不要对着自己或别人。"),
]

_COMBINED_PHRASES: List[Tuple[str, str]] = [
    ("safety.combined.01", "检测到可能摔倒并接近危险物品，请先不要动，等大人来帮忙。"),
    ("safety.combined.02", "请保持不动，手先离开危险物品，等待大人帮助。"),
    ("safety.combined.03", "现在可能不太安全，请不要自己站起来，也不要碰旁边的物品。"),
]

_UNKNOWN_PHRASES: List[Tuple[str, str]] = [
    ("safety.unknown.01", "现在可能不太安全，请先停一下，等待大人帮助。"),
]


def select_safety_phrase(
    analysis: SafetyAnalysis,
    candidate: Optional[SafetyCandidate],
) -> SafetyPhraseChoice:
    candidate_type = candidate.candidate_type if candidate else ""
    hazard = _hazard_name(candidate)
    seed = (candidate.event_id if candidate else "") + analysis.risk_type + candidate_type + hazard

    if candidate_type == "combined_candidate":
        phrase_id, text = _choose(_COMBINED_PHRASES, seed)
        return SafetyPhraseChoice(phrase_id, text, repeat_key="combined")

    if analysis.risk_type == "fall":
        phrase_id, text = _choose(_FALL_PHRASES, seed)
        return SafetyPhraseChoice(phrase_id, text, repeat_key="fall")

    if analysis.risk_type == "sharp_object":
        options = _phrases_for_hazard(hazard)
        phrase_id, text = _choose(options, seed)
        return SafetyPhraseChoice(phrase_id, text, repeat_key=f"sharp:{hazard or 'generic'}")

    phrase_id, text = _choose(_UNKNOWN_PHRASES, seed)
    return SafetyPhraseChoice(phrase_id, text, repeat_key=analysis.risk_type or "unknown")


def _choose(options: List[Tuple[str, str]], seed: str) -> Tuple[str, str]:
    if not options:
        return "safety.unknown.01", "现在可能不太安全，请先停一下，等待大人帮助。"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    idx = int(digest[:8], 16) % len(options)
    return options[idx]


def _phrases_for_hazard(hazard: str) -> List[Tuple[str, str]]:
    if hazard == "scissors":
        return _SCISSORS_PHRASES
    if hazard == "knife":
        return _KNIFE_PHRASES
    if hazard == "fork":
        return _FORK_PHRASES
    return _SHARP_GENERIC_PHRASES


def _hazard_name(candidate: Optional[SafetyCandidate]) -> str:
    if not candidate:
        return ""
    status = candidate.rknn_status or {}
    for relation in status.get("relations", []) or []:
        name = str(relation.get("hazard", "")).strip().lower()
        if name:
            return name
    for hazard in status.get("hazards", []) or []:
        name = str(hazard.get("name", "")).strip().lower()
        if name:
            return name
    return ""
