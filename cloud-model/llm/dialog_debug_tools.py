"""No-op tool executor for text-only conversation debugging."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from person_tasks import PERSON_TASK_TOOLS


BASE_DIALOG_DEBUG_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "take_photo",
            "description": "调试模式下模拟拍照；不会访问真实摄像头。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_brightness",
            "description": "调试模式下模拟光照传感器；真实传感器不可用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_motion",
            "description": "调试模式下模拟运动传感器；真实传感器不可用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_temperature",
            "description": "调试模式下模拟温湿度传感器；真实传感器不可用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

DIALOG_DEBUG_TOOLS = BASE_DIALOG_DEBUG_TOOLS + PERSON_TASK_TOOLS


def _text_payload(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    return [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}]


class DialogDebugToolExecutor:
    """Return deterministic no-op results for all robot system tools."""

    def __call__(self, name: str, args_str: str):
        try:
            args = json.loads(args_str or "{}")
        except json.JSONDecodeError:
            args = {}

        if name == "control_person_follow":
            return _text_payload({
                "ok": True,
                "simulated": True,
                "tool": name,
                "action": str(args.get("action") or ""),
                "target": str(args.get("target") or "nearest"),
                "message": "dialog_debug: motion command accepted but no robot system was called",
            })

        if name == "observe_people_identity":
            return _text_payload({
                "ok": True,
                "available": False,
                "simulated": True,
                "tool": name,
                "visible_people": [],
                "instruction": "调试模式没有摄像头和人脸数据库，请说明当前无法确认人物身份。",
            })

        unavailable_tools = {
            "take_photo": "camera",
            "get_brightness": "light_sensor",
            "get_motion": "motion_sensor",
            "get_temperature": "temperature_sensor",
        }
        if name in unavailable_tools:
            return _text_payload({
                "ok": False,
                "available": False,
                "simulated": True,
                "tool": name,
                "system": unavailable_tools[name],
                "message": f"dialog_debug: {unavailable_tools[name]} is unavailable in text-only debug mode",
            })

        return _text_payload({
            "ok": False,
            "available": False,
            "simulated": True,
            "tool": name,
            "message": "dialog_debug: unknown tool",
        })
