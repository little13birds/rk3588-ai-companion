"""多轮对话测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat import Conversation

print("=" * 50)
print("多轮对话测试 (summary_interval=3, keep_recent=2)")
print("输入 q 退出, stats 查看状态")
print("=" * 50)

conv = Conversation(
    system_prompt="你是一个友好的AI助手，用简体中文回答，回答尽量简洁。",
    max_history=8,
    summary_interval=0,
    keep_recent=2,
)

while True:
    try:
        user_input = input("\n你: ").strip()
    except (EOFError, KeyboardInterrupt):
        break

    if not user_input:
        continue
    if user_input.lower() == "q":
        break
    if user_input.lower() == "stats":
        conv.show_stats()
        continue

    print("AI: ", end="")
    conv.ask(user_input)

print("\n再见!")
