"""流式 TTS 测试 — LLM 边生成边说话，合成与播放流水线并行"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openai import OpenAI
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from tts.realtime_tts import RealtimeSpeaker

client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
speaker = RealtimeSpeaker(voice="Cherry")

print("=" * 58)
print("流式 TTS 测试 — 合成与播放并行，输入 q 退出")
print("=" * 58)

while True:
    try:
        user_input = input("\n你: ").strip()
    except (EOFError, KeyboardInterrupt):
        break
    if not user_input:
        continue
    if user_input.lower() == "q":
        break

    # LLM 流式生成
    print("AI: ", end="", flush=True)

    t_start = time.time()
    first_token = True
    ttft = 0

    stream = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": "你是AI语音助手，回复简洁，每句话不超过30字。"},
            {"role": "user", "content": user_input},
        ],
        stream=True,
    )

    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            token = chunk.choices[0].delta.content
            if first_token:
                ttft = (time.time() - t_start) * 1000
                first_token = False
            print(token, end="", flush=True)
            speaker.feed(token)

    speaker.flush()
    speaker.wait()
    total_time = (time.time() - t_start) * 1000

    stats = speaker.stats()
    print()
    print("-" * 58)
    print("  LLM思考(首字)   {:>8.0f} ms".format(ttft))
    print("  TTS首个合成     {:>8.0f} ms".format(stats["ttfa_ms"]))
    print("  TTS合成总耗时   {:>8.0f} ms ({}句)".format(
        stats["tts_ms"], stats["sentences"]))
    print("  端到端总耗时    {:>8.0f} ms".format(total_time))
    print("-" * 58)

print("\n再见!")
