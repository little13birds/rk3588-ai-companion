"""Deterministic voice intent fallback for high-risk person motion commands."""

from __future__ import annotations

from typing import Optional

from .roles import canonical_voice_target, extract_target_alias, normalize_target_text


FOLLOW_PHRASES = ("跟着我", "跟我走", "跟我来", "跟随我", "跟着", "跟随")
SEEK_PHRASES = ("找一下", "找找", "寻找", "在哪里", "在哪", "到我身边", "到我这里", "到身边")
TARGETED_SEEK_PHRASES = ("找", "身边", "过来", "过来找", "来我这", "来我这里")
STOP_PHRASES = (
    "不要跟了",
    "别跟了",
    "不用跟了",
    "不要跟着了",
    "别跟着了",
    "不用跟着了",
    "不要跟着我",
    "别跟着我",
    "别跟着我了",
    "不要跟随我",
    "不用跟随我",
    "别跟随我",
    "别跟随我了",
    "不要跟随",
    "不用跟随",
    "别跟随",
    "不跟随了",
    "停止跟随",
    "停下跟随",
    "退出跟随",
    "退出跟随模式",
    "关闭跟随",
    "关闭跟随模式",
    "结束跟随",
    "结束跟随模式",
    "停止寻找",
    "停止找人",
    "不要找了",
    "别找了",
    "不用找了",
)
OBSERVE_PHRASES = ("你知道我是谁吗", "我是谁", "你认识我吗", "前面都有谁", "面前都有谁", "你认识面前的人吗")


def parse_person_task_intent(text: str) -> Optional[dict]:
    normalized = normalize_target_text(text)
    if not normalized:
        return None

    if any(phrase in normalized for phrase in STOP_PHRASES):
        return {
            "tool": "control_person_follow",
            "args": {"action": "stop", "target": "nearest"},
        }

    if any(phrase in normalized for phrase in OBSERVE_PHRASES):
        return {"tool": "observe_people_identity", "args": {}}

    target = canonical_voice_target(extract_target_alias(normalized) or "")
    if any(phrase in normalized for phrase in FOLLOW_PHRASES):
        return {
            "tool": "control_person_follow",
            "args": {"action": "follow", "target": target or "nearest"},
        }

    if any(phrase in normalized for phrase in SEEK_PHRASES):
        return {
            "tool": "control_person_follow",
            "args": {"action": "seek", "target": target or "nearest"},
        }

    if target and any(phrase in normalized for phrase in TARGETED_SEEK_PHRASES):
        return {
            "tool": "control_person_follow",
            "args": {"action": "seek", "target": target},
        }

    return None
