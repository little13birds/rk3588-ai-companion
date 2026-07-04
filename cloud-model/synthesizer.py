"""云端 TTS 语音合成 + 流式播放 — qwen3-tts-flash"""
import sys, os, time, subprocess, tempfile, requests, threading, queue
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import TTS_API_KEY

TTS_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

VOICES = {
    "cherry": "Cherry",
    "adam": "Adam",
    "stella": "Stella",
    "sam": "Sam",
}

SENTENCE_ENDS = set("。！？\n")


def synthesize(text: str, voice: str = "Cherry",
               language: str = "Chinese", max_retries: int = 2,
               speed: float = 1.0) -> str:
    """合成语音，返回 WAV 文件路径。speed: 0.5~2.0, 1.0=原速。"""
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(
                TTS_URL,
                headers={
                    "Authorization": f"Bearer {TTS_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "qwen3-tts-flash",
                    "input": {
                        "text": text,
                        "voice": voice,
                        "language_type": language,
                    },
                },
                timeout=12,
            )
            resp.raise_for_status()
            data = resp.json()
            audio_url = data["output"]["audio"]["url"]
            audio_bytes = requests.get(audio_url, timeout=10).content
            break
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                print(f"  [TTS重试{attempt+1}] {e}", flush=True)
                time.sleep(0.5)
    else:
        raise RuntimeError(f"TTS合成失败(重试{max_retries}次): {last_err}")

    raw_path = tempfile.mktemp(suffix=".raw", prefix="tts_")
    wav_path = tempfile.mktemp(suffix=".wav", prefix="tts_")
    with open(raw_path, "wb") as f:
        f.write(audio_bytes)

    tmp_path = wav_path + ".tmp.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", raw_path, "-acodec", "pcm_s16le",
         "-ar", "16000", "-ac", "1", tmp_path],
        capture_output=True, timeout=10,
    )
    os.remove(raw_path)

    if speed != 1.0:
        tempo = min(2.0, max(0.5, speed))
        subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_path,
             "-filter:a", f"atempo={tempo}",
             "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", wav_path],
            capture_output=True, timeout=10,
        )
        os.remove(tmp_path)
    else:
        os.rename(tmp_path, wav_path)

    return wav_path


def play_audio(filepath: str, blocking: bool = True):
    """用 aplay 播放"""
    cmd = ["aplay", "-q", filepath]
    if blocking:
        subprocess.run(cmd, timeout=30)
    else:
        subprocess.Popen(cmd)


def speak(text: str, voice: str = "Cherry"):
    """合成并播放 — 非流式"""
    t0 = time.time()
    path = synthesize(text, voice=voice)
    t = (time.time() - t0) * 1000
    print(f"[TTS] {t:.0f}ms, {len(text)}字", flush=True)
    play_audio(path)


def _clear_queue(q: queue.Queue):
    """清空队列，对每个未完成任务调用 task_done"""
    try:
        while True:
            q.get_nowait()
            q.task_done()
    except queue.Empty:
        pass


class StreamSpeaker:
    """
    流水线语音合成播放器。
    接收 token → 按句子切分 → [合成线程] → [播放队列] → [播放线程]
    播放第 N 句的同时，后台合成第 N+1 句。
    支持 cancel() 打断当前播放。
    """

    def __init__(self, voice: str = "Cherry", speed: float = 1.0,
                 aec_filter=None):
        self.voice = voice
        self.speed = speed
        self.buffer = ""
        self._aec = aec_filter
        self._silence_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "audio", "silence.wav")
        self._sentence_queue = queue.Queue()
        self._audio_queue = queue.Queue()

        self._cancel_flag = threading.Event()
        self._player_proc = None
        self._player_lock = threading.Lock()

        self._synth_worker = threading.Thread(target=self._synth_loop, daemon=True)
        self._play_worker = threading.Thread(target=self._play_loop, daemon=True)
        self._synth_worker.start()
        self._play_worker.start()

        self._in_paren = False

        self.sentence_count = 0
        self.total_tts_ms = 0.0
        self.total_audio_ms = 0.0
        self._first_sentence_time = None
        self._start_time = time.time()

    def feed(self, text: str):
        """喂入 token，括号内容不进入 TTS"""
        for ch in text:
            if ch in "（(":
                self._in_paren = True
                continue
            if self._in_paren:
                if ch in "）)":
                    self._in_paren = False
                continue
            self.buffer += ch
        while True:
            idx = self._find_sentence_end()
            if idx == -1:
                break
            sentence = self.buffer[:idx + 1].strip()
            self.buffer = self.buffer[idx + 1:]
            if sentence:
                self._sentence_queue.put(sentence)

    def _find_sentence_end(self) -> int:
        for i, ch in enumerate(self.buffer):
            if ch in SENTENCE_ENDS:
                j = i + 1
                while j < len(self.buffer) and self.buffer[j] in SENTENCE_ENDS:
                    j += 1
                return j - 1
        return -1

    def _synth_loop(self):
        """合成线程：从句子队列取 → 调 API → 放入音频队列"""
        while True:
            sentence = self._sentence_queue.get()
            try:
                if self._cancel_flag.is_set():
                    self._sentence_queue.task_done()
                    continue
                t0 = time.time()
                path = synthesize(sentence, voice=self.voice,
                                  speed=self.speed)
                if self._cancel_flag.is_set():
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                else:
                    t = (time.time() - t0) * 1000
                    self.total_tts_ms += t
                    self.sentence_count += 1
                    if self._first_sentence_time is None:
                        self._first_sentence_time = time.time()
                    print("  [合成] {:>5.0f}ms | {}".format(t, sentence[:30]),
                          flush=True)
                    self._audio_queue.put(path)
                    if os.path.exists(self._silence_path):
                        self._audio_queue.put(self._silence_path)
            except Exception as e:
                print("  [合成错误] {}".format(e), flush=True)
            finally:
                self._sentence_queue.task_done()

    def _play_loop(self):
        """播放线程：从音频队列取 WAV → 喂AEC → aplay"""
        while True:
            path = self._audio_queue.get()
            if path is None:
                self._audio_queue.task_done()
                continue

            # 喂 AEC 参考信号（在播放之前）
            if self._aec:
                try:
                    self._aec.feed_wav(path)
                except Exception as e:
                    print("  [AEC feed错误] {}: {}".format(path, e), flush=True)

            proc = None
            try:
                with self._player_lock:
                    self._player_proc = subprocess.Popen(["aplay", "-q", path])
                    proc = self._player_proc
                proc.wait(timeout=60)
            except Exception:
                pass
            finally:
                with self._player_lock:
                    if self._player_proc is not None:
                        try:
                            if self._player_proc.poll() is None:
                                self._player_proc.kill()
                        except Exception:
                            pass
                        self._player_proc = None
                if path.startswith("/tmp/"):
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                self._audio_queue.task_done()

    def cancel(self):
        """打断：停止播放、清空队列、丢弃未合成句子"""
        self._cancel_flag.set()
        with self._player_lock:
            if self._player_proc and self._player_proc.poll() is None:
                try:
                    self._player_proc.kill()
                except Exception:
                    pass
                self._player_proc = None
        _clear_queue(self._sentence_queue)
        _clear_queue(self._audio_queue)
        self._audio_queue.put(None)

    def reset(self):
        """重置取消标志，恢复正常"""
        self._cancel_flag.clear()
        self.buffer = ""
        self._in_paren = False

    def flush(self):
        """强制输出 buffer 中剩余内容"""
        if self.buffer.strip():
            self._sentence_queue.put(self.buffer.strip())
            self.buffer = ""

    def queue_wav(self, filepath: str):
        """将预生成的 WAV 文件压入播放队列"""
        self._audio_queue.put(filepath)

    def wait(self):
        """等待所有句子合成+播放完毕"""
        self._sentence_queue.join()
        self._audio_queue.join()

    def stats(self):
        """返回流水线统计"""
        ttfa = 0
        if self._first_sentence_time:
            ttfa = (self._first_sentence_time - self._start_time) * 1000
        return {
            "sentences": self.sentence_count,
            "tts_ms": self.total_tts_ms,
            "ttfa_ms": ttfa,
        }
