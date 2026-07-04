"""VLM 视觉理解测试 — 流式输出 + 延时统计"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import base64
from openai import OpenAI
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)


def ask_image_stream(image_path: str, question: str):
    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("utf-8")

    t_start = time.time()
    stream = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            {"type": "text", "text": question},
        ]}],
        stream=True,
    )

    token_count = 0
    first_token_time = None
    text_buffer = ""

    print("=" * 50)
    print("[VLM 流式输出]")
    print("-" * 50)

    for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.content:
            if first_token_time is None:
                first_token_time = time.time()
            token_count += 1
            text_buffer += delta.content
            print(delta.content, end="", flush=True)

    t_end = time.time()
    ttft = first_token_time - t_start if first_token_time else 0
    total_time = t_end - t_start
    gen_time = t_end - first_token_time if first_token_time else 0
    tps = token_count / gen_time if gen_time > 0 else 0

    print()
    print("-" * 50)
    print(f"总字符数:       {len(text_buffer):>8}")
    print(f"Token 数:       {token_count:>8}")
    print(f"首包延迟(TTFT): {ttft*1000:>8.0f} ms")
    print(f"生成耗时:       {gen_time*1000:>8.0f} ms")
    print(f"总耗时:         {total_time*1000:>8.0f} ms")
    print(f"生成速度:       {tps:>8.1f} token/s")
    print("=" * 50)


if __name__ == "__main__":
    img = sys.argv[1] if len(sys.argv) > 1 else "/home/elf/Desktop/image_cropping/model/bus.jpg"
    q = sys.argv[2] if len(sys.argv) > 2 else "请用中文简单描述这张图片"
    ask_image_stream(img, q)
