"""用本地图片测试 VLM — base64 编码后上传"""
import base64
from openai import OpenAI
import os

client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY", ""),
    base_url="https://ws-t1b17pvoyg03mfwp.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
)

image_path = "/home/elf/Desktop/image_cropping/model/bus.jpg"
with open(image_path, "rb") as f:
    image_b64 = base64.b64encode(f.read()).decode("utf-8")

completion = client.chat.completions.create(
    model="qwen3-vl-flash",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                },
                {"type": "text", "text": "请用中文描述这张图片中有什么?"},
            ],
        },
    ],
)
print(completion.choices[0].message.content)
