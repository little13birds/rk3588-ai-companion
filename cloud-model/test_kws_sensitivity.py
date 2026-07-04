"""KWS 灵敏度测试 — 对比安静/播放环境下的唤醒词检测"""
import sys, os, time, subprocess, threading, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import SAMPLE_RATE, DEVICE_MIC
from asr.recognizer import ASRProcessor

CHUNK_BYTES = 320

# 固定长语录
LONG_TEXT = (
    "你好！这是一段比较长的测试文本，用于模拟语音合成播放。"
    "在这段音频播放的过程中，系统应该持续监听唤醒词。"
    "请尝试在播放过程中说出小智小智来进行测试。"
    "如果系统没有反应，请再试一次，并确保你的声音足够大。"
    "这段音频会持续大约十秒钟。"
)


def _synthesize(text, voice="Cherry"):
    """use RealtimeTTSSession to synth to WAV"""
    from tts.realtime_tts import RealtimeTTSSession, _pcm_to_wav
    session = RealtimeTTSSession(voice=voice)
    try:
        pcm = session.synthesize(text)
        return _pcm_to_wav(pcm, 24000)
    finally:
        session.close()



def start_mic(retries=5):
    for i in range(retries):
        p = subprocess.Popen(
            ["arecord", "-q", "-D", DEVICE_MIC,
             "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        time.sleep(0.1)
        if p.poll() is None:
            return p
        p.terminate()
        time.sleep(0.5)
    raise RuntimeError("麦克风无法打开")


def run_kws_test(label: str, play_audio: bool = False, duration: float = 8.0):
    """
    运行 KWS 检测测试
    label: 测试标签
    play_audio: 是否同时播放音频
    duration: 测试时长
    返回: 是否检测到唤醒词
    """
    mic = start_mic()
    detected = [False]
    detected_kw = [None]
    chunk_count = [0]

    def _on_wake(kw):
        detected[0] = True
        detected_kw[0] = kw
        print("\n!!! [KWS] 检测到: {} !!!".format(kw), flush=True)

    def _on_vad(speaking):
        pass

    def _on_result(r):
        pass

    asr = ASRProcessor(
        silence_timeout_ms=800,
        on_result=_on_result,
        on_wake=_on_wake,
        on_vad=_on_vad,
    )
    asr.start()
    # 保持 SLEEP 状态 (默认)，KWS 活跃

    print("[{}] ASR SLEEP, KWS 活跃".format(label))

    # 如果需要播放音频，启动后台播放
    player = None
    if play_audio:
        def play_tts():
            try:
                path = _synthesize(LONG_TEXT, voice="Cherry")
                print("[{}] TTS 合成完成，开始播放...".format(label))
                subprocess.run(["aplay", "-q", path], timeout=60)
                if os.path.exists(path):
                    os.remove(path)
            except Exception as e:
                print("[{}] 播放异常: {}".format(label, e))
        player = threading.Thread(target=play_tts, daemon=True)
        player.start()
        # 等合成+开始播放
        time.sleep(2.0)

    # 持续读 mic 喂 KWS
    t_start = time.time()
    print("[{}] 开始监听 {} 秒...".format(label, duration))
    print("[{}] 请现在说'小智小智'!".format(label))

    try:
        while time.time() - t_start < duration:
            try:
                raw = mic.stdout.read(CHUNK_BYTES)
            except Exception:
                time.sleep(0.1)
                continue
            if not raw:
                break
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            asr.feed(samples.tolist())
            chunk_count[0] += 1

            if detected[0]:
                break

            if chunk_count[0] % 100 == 0:
                elapsed = time.time() - t_start
                kc = getattr(asr, '_kws_chunks', 0)
                print("[{}] {:.1f}s KWS块={} qsize={}".format(
                    label, elapsed, kc, asr._audio_queue.qsize()), flush=True)

    except KeyboardInterrupt:
        pass
    finally:
        asr.stop()
        try:
            mic.terminate()
        except Exception:
            pass
        subprocess.run(["pkill", "-f", "aplay"], capture_output=True)

    return detected[0]


# ── 如果 TTS echo 太强，可以尝试在这里降低喇叭音量 ──
def set_volume(percent):
    """设置喇叭音量 (0-100)"""
    try:
        subprocess.run(["amixer", "sset", "Master", "{}%".format(percent)],
                       capture_output=True)
        print("[系统] 音量设为 {}%".format(percent))
    except Exception:
        pass


def main():
    print("=" * 55)
    print("KWS 灵敏度对比测试")
    print("=" * 55)

    # 降低音量以减少回声干扰（可调）
    set_volume(30)
    time.sleep(0.5)

    # ── 测试1: 安静环境（无播放） ──
    print("\n" + "=" * 55)
    print("测试1: 安静环境 — 无音频播放")
    print("请大声说'小智小智'")
    print("=" * 55)
    time.sleep(1)
    result1 = run_kws_test("安静", play_audio=False, duration=6.0)
    time.sleep(1)

    # ── 测试2: 播放环境（有TTS） ──
    print("\n" + "=" * 55)
    print("测试2: 播放环境 — TTS 正在播放")
    print("请在播放过程中大声说'小智小智'")
    print("=" * 55)
    time.sleep(1)
    result2 = run_kws_test("播放", play_audio=True, duration=12.0)

    # ── 结果 ──
    print("\n" + "=" * 55)
    print("测试结果:")
    print("  安静环境: {} (KWS检测到唤醒词)".format("PASS" if result1 else "FAIL"))
    print("  播放环境: {} (KWS检测到唤醒词)".format("PASS" if result2 else "FAIL"))
    if result1 and not result2:
        print()
        print("结论: KWS 本身工作正常，但 TTS 回声干扰了唤醒词检测")
        print("建议: 降低喇叭音量 / 加大麦克风与喇叭距离 / 使用 AEC")
    elif not result1:
        print()
        print("结论: KWS 即使在安静环境也无法检测，检查麦克风或KWS模型配置")
    else:
        print()
        print("结论: 两种环境都能检测，打断功能应正常工作")
    print("=" * 55)


if __name__ == "__main__":
    main()
