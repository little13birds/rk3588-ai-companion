"""VLM + TTS 融合测试 — 多轮对话 + 语音输出 + 输出长度限制"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from llm.chat import Conversation
from tts.realtime_tts import RealtimeSpeaker as StreamSpeaker

speaker = StreamSpeaker(voice="Cherry")

conv = Conversation(
    system_prompt="你是AI语音助手，每次回复1-3句话，简洁明了。不寒暄，不列举，不展开。",
    summary_interval=3,
    keep_recent=2,
    max_tokens=200,       # 限制输出长度
    speaker=speaker,      # 绑定 TTS
)

print("=" * 58)
print("VLM + TTS 融合测试 — 边想边说，输入 q 退出")
print("=" * 58)

IMG = "/home/elf/Desktop/image_cropping/model/bus.jpg"

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

    # 判断是否包含图片关键词
    if "图" in user_input and os.path.exists(IMG):
        print("AI: ", end="", flush=True)
        conv.ask_with_image(user_input, IMG)
    else:
        print("AI: ", end="", flush=True)
        conv.ask(user_input)

print("\n再见!")
