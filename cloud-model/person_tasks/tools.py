"""Tool schemas and execution helpers for person seek/follow tasks."""

from __future__ import annotations

import json
from typing import Optional

from .controller import PersonTaskController
from .roles import resolve_person_target


PERSON_TASK_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "control_person_follow",
            "description": (
                "控制机器人找人或跟随人。用户说跟着我、跟我走、跟我来时 target 用 nearest。"
                "用户说跟着角色A/A/tao 时 target 用 A；角色B/B/xiao 时 target 用 B。"
                "用户说找一下、在哪里、到身边来时 action 用 seek。停止跟随时 action 用 stop。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["follow", "seek", "stop"],
                    },
                    "target": {
                        "type": "string",
                        "description": "nearest、A、B、tao 或 xiao",
                    },
                },
                "required": ["action", "target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "observe_people_identity",
            "description": (
                "观察机器人面前的人物，并返回数据库识别到的身份。"
                "仅在用户问'我是谁'、'你认识我吗'、'前面都有谁'、'你认识面前的人吗'时调用。"
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


_DEFAULT_CONTROLLER: Optional[PersonTaskController] = None


def default_controller() -> PersonTaskController:
    global _DEFAULT_CONTROLLER
    if _DEFAULT_CONTROLLER is None:
        _DEFAULT_CONTROLLER = PersonTaskController()
    return _DEFAULT_CONTROLLER


def execute_person_tool(
    name: str,
    args_str: str,
    *,
    controller: Optional[PersonTaskController] = None,
):
    try:
        args = json.loads(args_str or "{}")
    except json.JSONDecodeError:
        args = {}
    active_controller = controller or default_controller()

    if name == "control_person_follow":
        action = str(args.get("action") or "").strip().lower()
        target_raw = str(args.get("target") or "nearest").strip()
        target = resolve_person_target(target_raw) or target_raw
        payload = active_controller.control(action, target)
        return [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}]

    if name == "observe_people_identity":
        payload = active_controller.observe_people()
        payload["instruction"] = (
            "请只根据 visible_people 回答。known=true 的人说出 name；known=false 的人说我不认识。"
            "如果有多个人，请逐个说明他们大致在 left/center/right。"
        )
        return [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}]

    return [{"type": "text", "text": json.dumps({"ok": False, "error": "unknown_person_tool", "name": name}, ensure_ascii=False)}]
