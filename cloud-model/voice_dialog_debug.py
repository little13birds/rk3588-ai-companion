"""Voice conversation debugger for cloud-model.

This entry point keeps the real microphone, KWS/ASR, TTS, and LLM conversation
loop, but replaces robot-facing tool calls with deterministic no-op results.
It is useful for testing wake-word and speech interaction without starting
camera, ROS, chassis, safety guard, dashboard, or reading-arm resources.
"""

from __future__ import annotations

import argparse
import queue
import re
import select
import subprocess
import sys
import threading
import time

from asr.recognizer import ASRProcessor
from audio.aec_filter import SharedAecFilter
from audio.fillers import set_speaker, think_filler, wake_reply
from config import DEVICE_MIC, SAMPLE_RATE
from llm.chat import Conversation
from llm.dialog_debug_tools import DIALOG_DEBUG_TOOLS, DialogDebugToolExecutor
from tts.realtime_tts import RealtimeSpeaker


CHUNK_BYTES = 320
IDLE_SLEEP_SEC = 5
WAKE_WORDS = {"你好小智", "小智小智"}
STOP_WORDS = {"停一下", "停一停", "停止", "暂停", "安静", "别说", "先别说"}
INTERRUPT_WORDS = STOP_WORDS

SYSTEM_PROMPT = (
    "你是小智的语音对话调试模式。你正在通过真实麦克风和真实语音合成与开发者对话，"
    "但摄像头、ROS、底盘、机械臂、传感器和人物识别工具全部都是空实现。"
    "当工具返回 unavailable 或 simulated 时，要明确说明这是调试模式限制，"
    "不要声称已经看到了真实画面、移动了机器人或读取到了真实传感器。"
    "回复保持简洁自然，输出纯文本。"
)

_NOISE_PATTERNS = [
    r"^[\.。,，!！?？;；:：\s\-—…~～'\"\+]+$",
    r"^(?i:yeah|yes|no|ok|oh|ah|um|uh|eh|mm|hm|ha|hey|hi|yo|wow|hmm|mhm|shh)[\.!！?？\s]*$",
    r"^[嗯啊哦哎唉诶嘿喂哼哈呀呢嘛吧]([\.。!！?？\s]*)$",
    r"^[぀-ゟ゠-ヿ]{1,3}([\.。!！?？\s]*)$",
    r"^[a-zA-Z]{1,3}[\.!！?？\s]*$",
]


def _is_noise(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if len(stripped) < 2:
        return True
    return any(re.match(pattern, stripped) for pattern in _NOISE_PATTERNS)


def _safe_print(message: str) -> None:
    try:
        print(message, flush=True)
    except KeyboardInterrupt:
        pass


def _safe_cleanup(func) -> None:
    try:
        func()
    except KeyboardInterrupt:
        pass
    except Exception:
        pass


def start_mic(retries: int = 5):
    for _idx in range(retries):
        proc = subprocess.Popen(
            [
                "arecord",
                "-q",
                "-D",
                DEVICE_MIC,
                "-f",
                "S16_LE",
                "-r",
                str(SAMPLE_RATE),
                "-c",
                "1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.1)
        if proc.poll() is None:
            return proc
        proc.terminate()
        time.sleep(0.5)
    raise RuntimeError("麦克风无法打开")


def drain_mic(mic_stdout, deadline_sec: float = 1.0) -> int:
    deadline = time.time() + deadline_sec
    drained = 0
    while time.time() < deadline:
        try:
            ready, _, _ = select.select([mic_stdout], [], [], 0.1)
        except Exception:
            break
        if not ready:
            break
        try:
            mic_stdout.read(CHUNK_BYTES * 10)
            drained += 1
        except Exception:
            break
    return drained


def build_conversation(speaker: RealtimeSpeaker, max_tokens: int) -> Conversation:
    return Conversation(
        system_prompt=SYSTEM_PROMPT,
        summary_interval=3,
        keep_recent=2,
        max_tokens=max_tokens,
        speaker=speaker,
        tools=DIALOG_DEBUG_TOOLS,
        tool_executor=DialogDebugToolExecutor(),
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run voice cloud-model conversation debug.")
    parser.add_argument("--max-tokens", type=int, default=200)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    print("=" * 55, flush=True)
    print("AI 语音调试助手 — ASR/TTS + no-op tools", flush=True)
    print("唤醒词: 你好小智 / 小智小智 | {}秒无对话回休眠".format(IDLE_SLEEP_SEC), flush=True)
    print("硬件工具: 全部模拟，不会启动相机、ROS、底盘或机械臂", flush=True)
    print("=" * 55, flush=True)

    aec = SharedAecFilter(sample_rate=SAMPLE_RATE, channels=1)
    speaker = RealtimeSpeaker(voice="Cherry", aec_filter=aec)
    set_speaker(speaker)
    conv = build_conversation(speaker=speaker, max_tokens=args.max_tokens)

    result_queue: queue.Queue = queue.Queue()
    cancel_event = threading.Event()
    state_lock = threading.Lock()
    state = {
        "processing": False,
        "interrupt": False,
        "auto_awake": False,
        "idle_since": time.time(),
    }

    def _get_state(name: str):
        with state_lock:
            return state[name]

    def _set_state(**updates):
        with state_lock:
            state.update(updates)

    def _on_wake(kw):
        _set_state(idle_since=time.time())
        processing = _get_state("processing")
        print("[voice_debug.wake] event=detected kw={} processing={} awake={}".format(
            kw, processing, asr.is_awake), flush=True)
        if processing:
            if kw in INTERRUPT_WORDS:
                print("\n>>> 打断: {} <<<".format(kw), flush=True)
                cancel_event.set()
                _set_state(interrupt=True)
            else:
                print("[voice_debug.wake] event=ignored reason=processing kw={}".format(kw), flush=True)
            return
        if kw in WAKE_WORDS:
            asr._request_state(True)
            threading.Thread(target=wake_reply, name="voice-debug-wake-reply", daemon=True).start()
            print("\n>>> 唤醒: {} <<<".format(kw), flush=True)
        else:
            print("[voice_debug.wake] event=ignored reason=sleep kw={}".format(kw), flush=True)

    def _on_vad(speaking: bool):
        if speaking:
            _set_state(idle_since=time.time())

    asr = ASRProcessor(
        silence_timeout_ms=800,
        on_result=lambda r: result_queue.put(r),
        on_wake=_on_wake,
        on_vad=_on_vad,
    )
    asr.start()
    mic = start_mic()
    print("[voice_debug] event=ready message=语音调试已启动，可以开始测试。", flush=True)

    def _process_utterance(text: str):
        try:
            speaker.reset()
            aec.reset()
            think_filler()
            print("小智: ", end="", flush=True)
            conv.ask(text, cancel_event=cancel_event)
        except Exception as exc:
            print("[voice_debug] event=ask_failed error_type={} error={}".format(
                type(exc).__name__, exc), flush=True)
        finally:
            if cancel_event.is_set():
                cancel_event.clear()
                print("[voice_debug] event=processing_interrupted", flush=True)
            _set_state(processing=False, auto_awake=True, idle_since=time.time())

    try:
        while True:
            try:
                raw = mic.stdout.read(CHUNK_BYTES)
            except Exception:
                mic.terminate()
                time.sleep(0.1)
                mic = start_mic()
                continue

            if not raw:
                mic.terminate()
                time.sleep(0.3)
                mic = start_mic()
                continue

            clean_raw = aec.process_mic(raw)
            asr.feed(bytes(clean_raw))

            try:
                result = result_queue.get_nowait()
            except queue.Empty:
                result = None

            if result and not _get_state("processing"):
                text = result.get("text", "").strip()
                if text in WAKE_WORDS:
                    _set_state(idle_since=time.time())
                    print("[voice_debug] event=filtered_control text={}".format(text), flush=True)
                    continue
                if text in STOP_WORDS:
                    asr.sleep()
                    speaker.cancel()
                    _set_state(auto_awake=False, idle_since=time.time())
                    print("[voice_debug] event=stop_listening text={}".format(text), flush=True)
                    continue
                if _is_noise(text):
                    if text:
                        print("[voice_debug] event=filtered_noise text={}".format(text), flush=True)
                    continue
                print("[识别] {}".format(text), flush=True)
                time.sleep(0.05)
                asr.sleep()
                _set_state(processing=True, idle_since=time.time())
                threading.Thread(target=_process_utterance, args=(text,), daemon=True).start()

            if _get_state("interrupt"):
                speaker.cancel()
                aec.reset()
                drained = drain_mic(mic.stdout, deadline_sec=1.0)
                while not asr._audio_queue.empty():
                    try:
                        asr._audio_queue.get_nowait()
                    except Exception:
                        break
                _set_state(processing=False, interrupt=False, auto_awake=False, idle_since=time.time())
                print("[voice_debug.interrupt] event=ready drained={}".format(drained), flush=True)

            if _get_state("auto_awake") and not _get_state("processing"):
                asr._request_state(True)
                _set_state(auto_awake=False, idle_since=time.time())
                print("[voice_debug] event=auto_awake status=listening", flush=True)

            if (
                asr.is_awake
                and not _get_state("processing")
                and time.time() - _get_state("idle_since") > IDLE_SLEEP_SEC
            ):
                print("\n[voice_debug] event=idle_sleep seconds={}".format(IDLE_SLEEP_SEC), flush=True)
                asr.sleep()
                _set_state(idle_since=time.time())

    except KeyboardInterrupt:
        _safe_print("\n[voice_debug] event=exit")
        return 0
    finally:
        _safe_cleanup(asr.stop)
        _safe_cleanup(mic.terminate)
        _safe_cleanup(speaker.cancel)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
