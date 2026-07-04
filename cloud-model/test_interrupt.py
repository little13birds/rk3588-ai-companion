"""打断功能独立测试 — 固定长语录 + KWS 监控"""
import sys, os, time, subprocess, threading, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import SAMPLE_RATE, DEVICE_MIC
from asr.recognizer import ASRProcessor
from tts.realtime_tts import RealtimeSpeaker as StreamSpeaker

CHUNK_BYTES = 320

LONG_TEXT = (
    "你好！这是一段打断功能测试音频。"
    "接下来我会说一段比较长的内容，用来模拟正常对话中的语音合成。"
    "在这个过程里，你可以随时说出小智小智来打断我。"
    "打断之后，系统应该立刻停止播放，并进入监听状态。"
    "如果你听到了这段话，说明打断功能还没有被触发。"
    "请再次尝试说出唤醒词小智小智。"
    "希望这次测试能够顺利通过。"
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


def main():
    print("=" * 55)
    print("打断功能独立测试 — SLEEP状态KWS检测")
    print("=" * 55)

    speaker = StreamSpeaker(voice="Cherry")
    mic = start_mic()

    interrupt_detected = False
    wake_count = 0
    loop_count = 0
    cancel_event = threading.Event()

    def _on_wake(kw):
        nonlocal interrupt_detected, wake_count
        wake_count += 1
        interrupt_detected = True
        print("\n!!! [KWS回调] 检测到: {} (第{}次) !!!".format(kw, wake_count), flush=True)
        cancel_event.set()

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
    # 不调 _set_awake，保持初始 SLEEP 状态，KWS 活跃
    print("[测试] ASR 已就绪，状态=SLEEP (KWS活跃)")
    print("[测试] audio_queue 初始大小={}".format(asr._audio_queue.qsize()))

    def player_thread():
        print("[播放] 开始合成固定语录...", flush=True)
        t0 = time.time()
        path = None
        try:
            path = _synthesize(LONG_TEXT, voice="Cherry")
            t_synth = time.time() - t0
            print("[播放] 合成完成 {:.1f}s，开始aplay...".format(t_synth), flush=True)
            subprocess.run(["aplay", "-q", path], timeout=120)
        except Exception as e:
            print("[播放] 异常: {}".format(e), flush=True)
        finally:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
            print("[播放] aplay结束", flush=True)

    print("\n测试步骤: 播放开始后，请大声说'小智小智'")
    print("3..."); time.sleep(1)
    print("2..."); time.sleep(1)
    print("1...开始!\n"); time.sleep(1)

    player = threading.Thread(target=player_thread, daemon=True)
    player.start()

    # 给播放线程时间合成 + 开始播放
    time.sleep(1.0)

    t_start = time.time()
    try:
        while player.is_alive() and not interrupt_detected:
            try:
                raw = mic.stdout.read(CHUNK_BYTES)
            except Exception:
                time.sleep(0.1)
                continue

            if not raw:
                break

            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            asr.feed(samples.tolist())
            loop_count += 1

            if loop_count % 100 == 0:
                elapsed = time.time() - t_start
                qs = asr._audio_queue.qsize()
                kc = asr._kws_chunks if hasattr(asr, '_kws_chunks') else '?'
                print("[监控] {:.1f}s 主循环={} KWS块={} qsize={} 打断={}".format(
                    elapsed, loop_count, kc, qs, interrupt_detected), flush=True)

            # 超时保护
            if time.time() - t_start > 30:
                print("[测试] 30秒超时", flush=True)
                break

    except KeyboardInterrupt:
        pass
    finally:
        asr.stop()
        try:
            mic.terminate()
        except Exception:
            pass
        subprocess.run(["pkill", "-f", "aplay"], capture_output=True)

    print()
    print("=" * 55)
    print("测试结果:")
    print("  主循环次数: {}".format(loop_count))
    print("  KWS处理块: {}".format(asr._kws_chunks if hasattr(asr, '_kws_chunks') else '?'))
    print("  KWS检测: {} 次".format(wake_count))
    print("  最终audio_queue大小: {}".format(asr._audio_queue.qsize()))
    if interrupt_detected:
        print("  => 打断功能正常! KWS 在播放期间检测到了唤醒词")
    else:
        print("  => 打断失败: KWS 未检测到唤醒词")
        print("  可能原因: 1.回声干扰 2.KWS阈值 3.麦克风问题")
    print("=" * 55)


if __name__ == "__main__":
    main()
