"""打断链路自动化测试 — 程序模拟 on_wake 回调，无需人声"""
import sys, os, time, subprocess, threading, queue, select, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import SAMPLE_RATE, DEVICE_MIC
from asr.recognizer import ASRProcessor
from tts.realtime_tts import RealtimeSpeaker as StreamSpeaker
from audio.aec_filter import SharedAecFilter

CHUNK_BYTES = 320

LONG_TEXT = (
    "你好！这是一段打断功能测试音频。"
    "接下来我会说一段比较长的内容。"
    "总共大约需要播放十秒钟左右。"
    "在这个过程中系统应该能够正常检测打断。"
    "如果打断功能正常，播放会在中途被终止。"
    "希望这次自动化测试能够顺利通过验证。"
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


def drain_mic(mic_stdout, deadline_sec=1.0):
    deadline = time.time() + deadline_sec
    drained = 0
    while time.time() < deadline:
        try:
            ready, _, _ = select.select([mic_stdout], [], [], 0.1)
        except Exception:
            break
        if ready:
            try:
                mic_stdout.read(CHUNK_BYTES * 10)
                drained += 1
            except Exception:
                break
        else:
            break
    return drained


def test_interrupt_chain():
    """
    自动化测试打断链路:
    1. ASR 置为 SLEEP (模拟 asr.sleep() 后的状态)
    2. 启动 TTS 播放
    3. 3秒后程序模拟 on_wake 回调
    4. 验证 speaker.cancel() 生效 + state 重置 + drain 执行
    """
    print("=" * 55)
    print("打断链路自动化测试")
    print("=" * 55)

    aec = SharedAecFilter(sample_rate=SAMPLE_RATE, channels=1)
    speaker = StreamSpeaker(voice="Cherry", aec_filter=aec)
    mic = start_mic()

    is_processing = True  # 模拟处理中
    cancel_event = threading.Event()
    interrupt_requested = False
    auto_awake_requested = False
    test_passed = True

    def _on_wake(kw):
        nonlocal interrupt_requested
        print("[_on_wake] 模拟回调触发 kw={} is_processing={}".format(kw, is_processing))
        if is_processing:
            interrupt_requested = True
            cancel_event.set()
            print("[_on_wake] interrupt_requested=True cancel_event.set()")

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
    # 初始 SLEEP 状态（模拟 asr.sleep() 之后）
    print("[测试] ASR SLEEP状态 is_awake={}".format(asr.is_awake))

    # ── 模拟 process_utterance 线程 ──
    def simulated_processor():
        nonlocal is_processing, auto_awake_requested
        speaker.reset()
        print("[处理线程] 开始合成固定语录...")
        path = _synthesize(LONG_TEXT, voice="Cherry")
        print("[处理线程] 合成完成，queuing...")
        speaker.queue_wav(path)  # 用 queue_wav 而非直接 aplay
        speaker.flush()
        print("[处理线程] 等待播放完毕...")
        try:
            speaker.wait()
        except Exception as e:
            print("[处理线程] wait异常: {}".format(e))
        if cancel_event.is_set():
            print("[处理线程] 检测到打断标志，退出")
            cancel_event.clear()
        else:
            print("[处理线程] 正常播完")
            is_processing = False
            auto_awake_requested = True

    def handle_interrupt():
        nonlocal is_processing, interrupt_requested
        print("\n[handle_interrupt] 开始处理打断...")
        # 1. cancel speaker
        t0 = time.time()
        speaker.cancel()
        t_cancel = time.time() - t0
        print("[handle_interrupt] speaker.cancel() 耗时 {:.3f}s".format(t_cancel))

        # 2. drain mic
        drained = drain_mic(mic.stdout, deadline_sec=1.0)
        print("[handle_interrupt] drain_mic: {}块".format(drained))

        # 3. reset speaker
        speaker.reset()

        # 4. force_awake
        asr.force_awake()
        print("[handle_interrupt] force_awake is_awake={}".format(asr.is_awake))

        # 5. reset state
        is_processing = False
        interrupt_requested = False
        auto_awake_requested = False
        print("[handle_interrupt] 完成\n")

    # ── 启动 ──
    proc_thread = threading.Thread(target=simulated_processor, daemon=True)
    proc_thread.start()

    # 给 TTS 合成 + 开始播放的时间
    time.sleep(0.5)
    print("[测试] 等待TTS合成...")

    # 等 proc_thread 到 speaker.wait() (即 TTS 正在播放)
    proc_thread.join(timeout=5)
    if proc_thread.is_alive():
        print("[测试] 处理线程仍在运行 (TTS播放中)，模拟打断...")
        # 程序模拟 KWS 检测到唤醒词
        _on_wake("小智小智")

        # 模拟主循环：检查 interrupt_requested
        print("[模拟主循环] interrupt_requested={}".format(interrupt_requested))
        if interrupt_requested:
            handle_interrupt()

        # 等待处理线程退出
        proc_thread.join(timeout=3)
        if proc_thread.is_alive():
            print("[FAIL] 处理线程在打断后仍未退出！")
            test_passed = False
        else:
            print("[OK] 处理线程已退出")

        # 验证 is_processing 已重置
        if is_processing:
            print("[FAIL] is_processing 仍为 True")
            test_passed = False
        else:
            print("[OK] is_processing 已重置为 False")

        # 验证 ASR 状态为 AWAKE
        if not asr.is_awake:
            print("[FAIL] asr.is_awake 应为 True 实际为 {}".format(asr.is_awake))
            test_passed = False
        else:
            print("[OK] asr.is_awake=True 已就绪")
    else:
        print("[WARN] 处理线程已退出（TTS可能太短，未等到打断窗口）")
        test_passed = False

    # 清理
    asr.stop()
    try:
        mic.terminate()
    except Exception:
        pass
    subprocess.run(["pkill", "-f", "aplay"], capture_output=True)

    print()
    if test_passed:
        print("=" * 55)
        print("打断链路测试 PASSED")
        print("=" * 55)
    else:
        print("=" * 55)
        print("打断链路测试 FAILED")
        print("=" * 55)
    return test_passed


if __name__ == "__main__":
    ok = test_interrupt_chain()
    sys.exit(0 if ok else 1)
