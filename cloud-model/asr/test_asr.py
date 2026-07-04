"""ASR 测试 — 麦克风录音 → ASR识别"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import subprocess, time
import numpy as np
from recognizer import ASRProcessor, SAMPLE_RATE

print("=" * 50)
print("ASR 测试 — 本地 SenseVoice + 唤醒词")
print("=" * 50)

def on_result(r):
    print(f"\n[识别] {r['text']}")

def on_wake(kw):
    print(f"\n>>> 唤醒词: {kw} <<<")

def on_vad(speaking):
    pass  # 静默，避免刷屏

asr = ASRProcessor(
    silence_timeout_ms=800,
    on_result=on_result,
    on_wake=on_wake,
    on_vad=on_vad,
)
asr.start()

print("启动中，请说话... 按 Ctrl+C 退出")
print("唤醒词: 你好小智 / 小智小智")
print("-" * 50)

# 用 arecord 录音并转为 float32
try:
    # 录音参数: 16kHz, mono, 16bit → 16bit int samples
    proc = subprocess.Popen(
        ["arecord", "-q", "-D", "plughw:Audio,0",
         "-f", "S16_LE", "-r", "16000", "-c", "1"],
        stdout=subprocess.PIPE,
    )

    while True:
        # 每次读 160 样本 (10ms) → int16 → float32
        raw = proc.stdout.read(320)  # 160 * 2 bytes
        if not raw:
            break
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        asr.feed(samples.tolist())

except KeyboardInterrupt:
    pass
finally:
    proc.terminate()
    asr.stop()
    print("\n结束")
