"""Role aliases for voice-controlled identity tasks."""

from __future__ import annotations

import re
from typing import Optional


ROLE_TO_PERSON = {
    "A": "tao",
    "a": "tao",
    "角色a": "tao",
    "角色A": "tao",
    "tao": "tao",
    "涛": "tao",
    "B": "xiao",
    "b": "xiao",
    "角色b": "xiao",
    "角色B": "xiao",
    "xiao": "xiao",
    "小": "xiao",
}

NEAREST_ALIASES = {"我", "自己", "最近的人", "最近", "nearest", "me"}


def normalize_target_text(value: str) -> str:
    text = str(value or "").strip()
    text = text.replace(" ", "")
    text = text.replace("Ａ", "A").replace("Ｂ", "B")
    text = text.replace("ａ", "a").replace("ｂ", "b")
    return text


def resolve_person_target(value: str) -> Optional[str]:
    text = normalize_target_text(value)
    if not text:
        return None
    if text in NEAREST_ALIASES:
        return "nearest"
    if text in ROLE_TO_PERSON:
        return ROLE_TO_PERSON[text]
    lower = text.lower()
    if lower in ROLE_TO_PERSON:
        return ROLE_TO_PERSON[lower]
    return None


def extract_target_alias(text: str) -> Optional[str]:
    normalized = normalize_target_text(text)
    candidates = sorted(
        set(ROLE_TO_PERSON) | NEAREST_ALIASES,
        key=len,
        reverse=True,
    )
    for candidate in candidates:
        if candidate and candidate in normalized:
            return candidate
    match = re.search(r"角色([A-Ba-b])", normalized)
    if match:
        return "角色" + match.group(1).upper()
    return None


def canonical_voice_target(value: str) -> Optional[str]:
    text = normalize_target_text(value)
    if not text:
        return None
    if text in NEAREST_ALIASES:
        return "nearest"
    resolved = resolve_person_target(text)
    if resolved == "tao":
        return "A"
    if resolved == "xiao":
        return "B"
    return resolved
