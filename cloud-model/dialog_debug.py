"""Text-only conversation debugger for cloud-model.

This entry point runs the LLM conversation loop without ASR, TTS, dashboard,
safety guard, camera, ROS, sensors, or chassis control. Tool calls are handled
by deterministic no-op results so prompt and function-calling behavior can be
tested without touching robot resources.
"""

from __future__ import annotations

import argparse
import sys

from llm.chat import Conversation
from llm.dialog_debug_tools import DIALOG_DEBUG_TOOLS, DialogDebugToolExecutor


DIALOG_DEBUG_SYSTEM_PROMPT = (
    "你是小智的对话调试模式。你正在通过纯文本和开发者对话。"
    "你可以调用工具来验证 function calling 路径，但所有工具都是空实现："
    "硬件、摄像头、ROS、底盘、传感器和人物识别都不会真的执行。"
    "当工具返回 unavailable 时，要明确说明这是对话调试模式限制，"
    "不要声称已经看到了真实画面或读取到了真实传感器。"
    "回复保持简洁自然，输出纯文本。"
)


def build_conversation(max_tokens: int = 200) -> Conversation:
    return Conversation(
        system_prompt=DIALOG_DEBUG_SYSTEM_PROMPT,
        summary_interval=3,
        keep_recent=2,
        max_tokens=max_tokens,
        tools=DIALOG_DEBUG_TOOLS,
        tool_executor=DialogDebugToolExecutor(),
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run text-only cloud-model conversation debug.")
    parser.add_argument("--max-tokens", type=int, default=200)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    conv = build_conversation(max_tokens=args.max_tokens)

    print("[dialog_debug] event=ready mode=text_only", flush=True)
    print("Commands: /exit /quit /stats /reset", flush=True)
    while True:
        try:
            text = input("dialog> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[dialog_debug] event=exit", flush=True)
            return 0

        if not text:
            continue
        if text in {"/exit", "/quit", "exit", "quit"}:
            print("[dialog_debug] event=exit", flush=True)
            return 0
        if text == "/stats":
            conv.show_stats()
            continue
        if text == "/reset":
            conv = build_conversation(max_tokens=args.max_tokens)
            print("[dialog_debug] event=reset", flush=True)
            continue

        print("小智: ", end="", flush=True)
        try:
            conv.ask(text)
        except Exception as exc:
            print(
                "\n[dialog_debug] event=ask_failed error_type={} error={}".format(
                    type(exc).__name__,
                    exc,
                ),
                flush=True,
            )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
