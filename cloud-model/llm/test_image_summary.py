"""测试图片摘要压缩 — 验证图片+名字在压缩后不丢失"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat import Conversation

IMG = "/home/elf/Desktop/image_cropping/model/bus.jpg"

conv = Conversation(summary_interval=2, keep_recent=1)

print("=" * 55)
print("测试: 图片摘要压缩 (summary_interval=2, keep_recent=1)")
print("第1-2轮塞入名字+图片, 第3轮触发压缩, 第4轮验证")
print("=" * 55)

conv.ask("我叫李四，今年30岁")
conv.ask_with_image("这是什么？", IMG)
conv.ask("图中有什么交通工具？")   # ← 触发压缩
conv.ask("我叫什么名字？多大？图中是什么？")  # ← 验证记忆

print()
conv.show_stats()
