"""实时 TTS 语音合成 + 流式播放 — qwen3-tts-flash-realtime (WebSocket)"""
import sys, os, time, re, base64, struct, wave, tempfile, subprocess, threading, queue
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import TTS_API_KEY, DEVICE_SPK, TTS_REALTIME_SAMPLE_RATE, TTS_REALTIME_SILENCE_MS, TTS_REALTIME_TIMEOUT, TTS_REALTIME_MAX_RETRIES
import dashscope
dashscope.api_key = TTS_API_KEY
from dashscope.audio.qwen_tts_realtime import (
    QwenTtsRealtime, QwenTtsRealtimeCallback, AudioFormat
)

# 复用 synthesizer.py 中的 VOICES / 切句逻辑
from tts.synthesizer import VOICES, SENTENCE_ENDS, VOICE_TAG_RE, _clear_queue, split_tts_text
from tts.phrase_cache import PhraseCacheKey, get_or_create_wav
from utils.console_io import console_print
from utils.tts_debug_log import log_tts_event, text_tail

# 句间静音 PCM（24kHz 16bit mono）
_SILENCE_BYTES = b'\x00' * (TTS_REALTIME_SAMPLE_RATE * 2 * TTS_REALTIME_SILENCE_MS // 1000)

# speed → instructions 映射（实时 TTS 不支持调速参数，用自然语言指令替代）
_SPEED_INSTRUCTIONS = {
    1.0: "",
    0.9: "语速偏慢",
    0.8: "语速偏慢",
}

REALTIME_TTS_MODEL = "qwen3-tts-instruct-flash-realtime"
WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"


class _SessionCallback(QwenTtsRealtimeCallback):
    """内部回调：收集音频 delta 并在 response.done / on_close 时通知"""

    def __init__(self):
        super().__init__()
        self.response_done = threading.Event()
        self.audio_chunks = []   # list[bytes]
        self.error = None
        self.session_id = None
        self._lock = threading.Lock()  # 保护 audio_chunks / error

    def on_open(self) -> None:
        pass

    def on_close(self, close_status_code, close_msg) -> None:
        # 确保 cancel/close 场景下等待方不被死锁
        self.response_done.set()

    def on_event(self, response: dict) -> None:
        try:
            event_type = response.get("type", "")
            if event_type == "session.created":
                self.session_id = response.get("session", {}).get("id", "")
            elif event_type == "response.audio.delta":
                b64 = response.get("delta", "")
                if b64:
                    with self._lock:
                        self.audio_chunks.append(base64.b64decode(b64))
            elif event_type == "response.done":
                self.response_done.set()
            elif event_type == "session.finished":
                self.response_done.set()
        except Exception as e:
            with self._lock:
                self.error = str(e)
            self.response_done.set()

    def reset(self):
        """重置状态，准备下一次 commit"""
        with self._lock:
            self.response_done.clear()
            self.audio_chunks.clear()
            self.error = None


class RealtimeTTSSession:
    """封装单个 WebSocket 连接，管理 append/commit 生命周期，支持音色切换"""

    def __init__(self, voice: str = "Cherry", instructions: str = "", api_key: str = TTS_API_KEY):
        self._voice = voice
        self._instructions = instructions
        self._api_key = api_key
        self._callback = _SessionCallback()
        self._qwen = None
        self._lock = threading.Lock()  # 保护 _qwen 访问（cancel / synthesize 并发安全）
        # 懒加载：首次 synthesize() 时连接

    def _connect(self):
        """建立 WebSocket 连接并初始化 session"""
        self._callback.reset()
        self._qwen = QwenTtsRealtime(
            model=REALTIME_TTS_MODEL,
            callback=self._callback,
            url=WS_URL,
        )
        self._qwen.connect()
        self._qwen.update_session(
            voice=self._voice,
            instructions=self._instructions,
            response_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
            mode="commit",
        )

    def switch_voice(self, new_voice: str, instructions: str = ""):
        """切换音色 — API 不支持 mid-session update_session，故断开重连"""
        if new_voice == self._voice:
            return
        self._voice = new_voice
        self._instructions = instructions
        with self._lock:
            if self._qwen:
                try:
                    self._qwen.close()
                except Exception:
                    pass
                self._qwen = None
            self._callback.reset()

    def synthesize(self, text: str) -> bytes:
        """
        合成一句话：append_text → commit → 等 response.done → 返回 PCM bytes。
        超时或错误时自动重试（最多 TTS_REALTIME_MAX_RETRIES 次）。
        """
        if not text.strip() or not re.search(r'[a-zA-Z0-9\u4e00-\u9fff]', text.strip()):
            return b""

        last_err = None
        for attempt in range(TTS_REALTIME_MAX_RETRIES + 1):
            try:
                with self._lock:
                    if self._qwen is None:
                        self._connect()
                    self._callback.reset()
                    self._qwen.append_text(text)
                    self._qwen.commit()

                if not self._callback.response_done.wait(timeout=TTS_REALTIME_TIMEOUT):
                    raise TimeoutError(f"TTS commit 超时 ({TTS_REALTIME_TIMEOUT}s): {text[:30]}")

                if self._callback.error:
                    raise RuntimeError(f"TTS 回调错误: {self._callback.error}")

                pcm = b"".join(self._callback.audio_chunks)
                if not pcm:
                    raise RuntimeError(f"TTS 返回空音频: {text[:30]}")
                return pcm
            except Exception as e:
                last_err = e
                if attempt < TTS_REALTIME_MAX_RETRIES:
                    console_print(
                        "[tts.realtime] event=retry attempt={} max_retries={} error={}".format(
                            attempt + 1,
                            TTS_REALTIME_MAX_RETRIES,
                            str(e).replace("\n", " ")[:220],
                        ),
                        flush=True,
                        defer_during_stream=True,
                    )
                    time.sleep(0.5)
                    try:
                        self.cancel()
                    except Exception:
                        pass
        raise RuntimeError(f"TTS实时合成失败(重试{TTS_REALTIME_MAX_RETRIES}次): {last_err}")

    def cancel(self):
        """打断：关闭 WebSocket，下次 synthesize 自动重连"""
        with self._lock:
            try:
                if self._qwen:
                    self._qwen.close()
            except Exception:
                pass
            finally:
                self._qwen = None
        self._callback.response_done.set()  # 解阻塞任何正在等待的 synthesize()

    def close(self):
        """正常关闭"""
        with self._lock:
            try:
                if self._qwen:
                    self._qwen.finish()
                    self._qwen.close()
            except Exception:
                pass
            finally:
                self._qwen = None

    @property
    def voice(self):
        return self._voice


# ─── PCM ↔ WAV 工具 ───────────────────────────────────────────

def _pcm_to_wav(pcm_data: bytes, sample_rate: int) -> str:
    """将 PCM bytes 写入临时 WAV 文件（含 44 字节头部），返回路径。用于 AEC feed_wav()。"""
    path = tempfile.mktemp(suffix=".wav", prefix="aec_")
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return path


def _resample_pcm(pcm_data: bytes, src_rate: int, dst_rate: int) -> bytes:
    """PCM 重采样（numpy 线性插值），src_rate → dst_rate。"""
    if src_rate == dst_rate:
        return pcm_data
    samples = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float64)
    n_in = len(samples)
    n_out = int(n_in * dst_rate / src_rate)
    if n_out == 0:
        return b""
    out = np.interp(
        np.linspace(0, n_in - 1, n_out),
        np.arange(n_in),
        samples,
    ).astype(np.int16)
    return out.tobytes()



# ─── 音量归一化 ──────────────────────────────────────────

FILLER_WAV_GAIN = 1.6


def _normalize_volume(pcm: bytes, target_rms: float = 4000, max_gain: float = 4.0) -> bytes:
    """将 PCM 音量归一化到目标 RMS 水平（16-bit 参考值 32768）"""
    if len(pcm) < 2:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    rms = float(np.sqrt(np.mean(samples ** 2)))
    if rms < 10:  # 几乎静音，不动
        return pcm
    gain = min(target_rms / rms, max_gain)
    if 0.9 < gain < 1.1:  # 接近目标，跳过避免质量损失
        return pcm
    samples = (samples * gain).clip(-32768, 32767).astype(np.int16)
    return samples.tobytes()


def _amplify_wav(filepath: str, gain: float = FILLER_WAV_GAIN) -> str:
    """Create a temporary amplified WAV for fixed filler audio playback."""
    if gain <= 1.01:
        return filepath
    try:
        with wave.open(filepath, "rb") as src:
            params = src.getparams()
            frames = src.readframes(src.getnframes())
        if params.sampwidth != 2 or params.nchannels != 1 or not frames:
            return filepath
        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float64)
        amplified = (samples * gain).clip(-32768, 32767).astype(np.int16)
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name
        tmp.close()
        with wave.open(tmp_path, "wb") as dst:
            dst.setparams(params)
            dst.writeframes(amplified.tobytes())
        return tmp_path
    except Exception as e:
        console_print("[tts] event=wav_gain_error path={} error={}".format(filepath, e), flush=True, defer_during_stream=True)
        return filepath


def _console_text(text: str) -> str:
    return str(text or "").replace("\n", "\\n")

# ─── RealtimeSpeaker ──────────────────────────────────────────

class RealtimeSpeaker:
    """
    实时 WebSocket 流水线语音合成播放器。
    接口与 StreamSpeaker 兼容，可直接替换。
    接收 (voice, text) 句子 → [合成线程: WebSocket] → [播放队列: PCM] → [播放线程: aplay stdin]
    支持 cancel() 打断、queue_wav() 兼容预生成 WAV。
    """

    def __init__(self, voice: str = "Cherry", speed: float = 1.0,
                 aec_filter=None):
        self.voice = voice
        self._active_voice = voice
        self.speed = speed          # 实时 TTS 不支持调速，保留参数但忽略
        self._aec = aec_filter
        self.buffer = ""
        self._in_paren = False

        self._sentence_queue = queue.Queue()
        self._audio_queue = queue.Queue()   # ("pcm", bytes) | ("silence", None) | ("wav", str) | None

        self._cancel_flag = threading.Event()
        self._player_proc = None
        self._player_lock = threading.Lock()
        self._sentence_id = 0
        self._sentence_id_lock = threading.Lock()

        # WebSocket session — 首次合成时懒连接
        self._session = None
        self._session_voice = None
        self._phrase_session = None
        self._phrase_session_voice = None
        self._phrase_session_instructions = None
        self._phrase_lock = threading.Lock()

        self._synth_worker = threading.Thread(target=self._synth_loop, daemon=True)
        self._play_worker = threading.Thread(target=self._play_loop, daemon=True)
        self._synth_worker.start()
        self._play_worker.start()

        self.sentence_count = 0
        self.total_tts_ms = 0.0
        self._first_sentence_time = None
        self._start_time = time.time()

    # ── 切句逻辑（与 StreamSpeaker 完全相同）────────────────

    def _next_sentence_id(self) -> int:
        with self._sentence_id_lock:
            self._sentence_id += 1
            return self._sentence_id

    def _queue_sentence(self, voice: str, text: str, source: str) -> None:
        sentence = (text or "").strip()
        if not sentence:
            return
        segments = split_tts_text(
            sentence,
            ensure_terminal_punctuation=True,
        )
        for index, segment in enumerate(segments):
            sentence_id = self._next_sentence_id()
            segment_source = source if len(segments) == 1 else f"{source}:segment"
            log_tts_event(
                "tts_queue_sentence",
                sentence_id=sentence_id,
                source=segment_source,
                segment_index=index,
                segment_count=len(segments),
                voice=voice,
                text=segment,
                original_text=sentence if len(segments) > 1 else None,
                text_tail=text_tail(segment, 160),
                queue_size=self._sentence_queue.qsize(),
            )
            self._sentence_queue.put((sentence_id, voice, segment, segment_source))

    def feed(self, text: str):
        """喂入 token，[VoiceName]为主分隔符切句，括号和emoji不进入 TTS"""
        raw_text = str(text or "")
        for ch in text:
            cp = ord(ch)
            if cp > 0x2000 and (0x1F000 <= cp <= 0x1FAFF
                                or 0x2600 <= cp <= 0x27BF
                                or 0x2300 <= cp <= 0x23FF
                                or 0xFE00 <= cp <= 0xFE0F
                                or 0x1F900 <= cp <= 0x1F9FF
                                or 0x2702 <= cp <= 0x27B0
                                or 0x1F600 <= cp <= 0x1F64F):
                continue
            if ch in "*#_~`":
                continue
            if ch in "（(":
                self._in_paren = True
                continue
            if self._in_paren:
                if ch in "）)":
                    self._in_paren = False
                continue
            self.buffer += ch
        log_tts_event(
            "tts_feed",
            raw_tail=text_tail(raw_text, 80),
            buffer_tail=text_tail(self.buffer, 120),
            active_voice=self._active_voice,
            in_paren=self._in_paren,
        )
        while True:
            m = VOICE_TAG_RE.search(self.buffer)
            if m:
                pre = self.buffer[:m.start()].strip()
                if pre:
                    self._queue_sentence(self._active_voice, pre, "voice_tag")
                tag = next((k for k in VOICES if k.lower() == m.group(1).lower()), None)
                if tag:
                    old_voice = self._active_voice
                    self._active_voice = VOICES[tag]
                    log_tts_event(
                        "tts_voice_tag",
                        tag=m.group(1),
                        old_voice=old_voice,
                        new_voice=self._active_voice,
                        buffer_tail=text_tail(self.buffer[m.end():], 120),
                    )
                    self.buffer = self.buffer[m.end():]
                    continue
                else:
                    self.buffer = self.buffer[1:]
                    continue
            idx = self._find_sentence_end()
            if idx == -1:
                break
            sentence = self.buffer[:idx + 1].strip()
            self.buffer = self.buffer[idx + 1:]
            if sentence:
                self._queue_sentence(self._active_voice, sentence, "sentence_end")

    def _find_sentence_end(self) -> int:
        for i, ch in enumerate(self.buffer):
            if ch in SENTENCE_ENDS:
                j = i + 1
                while j < len(self.buffer) and self.buffer[j] in SENTENCE_ENDS:
                    j += 1
                return j - 1
        return -1

    # ── 合成线程 ────────────────────────────────────────────

    def _ensure_session(self, voice: str):
        """确保 WebSocket 已连接且音色匹配"""
        inst = _SPEED_INSTRUCTIONS.get(self.speed, "")
        if self._session is None:
            self._session = RealtimeTTSSession(voice=voice, instructions=inst)
            self._session_voice = voice
        elif voice != self._session_voice:
            self._session.switch_voice(voice, instructions=inst)
            self._session_voice = voice

    def _synth_loop(self):
        """合成线程：取 (voice, text) → WebSocket commit → PCM → 放入播放队列"""
        while True:
            item = self._sentence_queue.get()
            try:
                if self._cancel_flag.is_set():
                    continue

                if isinstance(item, tuple) and len(item) == 4:
                    sentence_id, voice, text, source = item
                elif isinstance(item, tuple) and len(item) == 2:
                    sentence_id = self._next_sentence_id()
                    voice, text = item
                    source = "legacy"
                else:
                    sentence_id = self._next_sentence_id()
                    voice, text, source = self._active_voice, item, "legacy"
                if not text.strip() or not re.search(r'[a-zA-Z0-9\u4e00-\u9fff]', text.strip()):
                    log_tts_event(
                        "tts_synth_skip",
                        sentence_id=sentence_id,
                        source=source,
                        voice=voice,
                        reason="empty_or_no_text_chars",
                        text_tail=text_tail(text, 120),
                    )
                    continue

                t0 = time.time()
                self._ensure_session(voice)
                pcm = self._session.synthesize(text)
                pcm = _normalize_volume(pcm)
                duration_ms = len(pcm) / max(1, TTS_REALTIME_SAMPLE_RATE * 2) * 1000

                if self._cancel_flag.is_set():
                    log_tts_event(
                        "tts_synth_discard_after_cancel",
                        sentence_id=sentence_id,
                        source=source,
                        voice=voice,
                        pcm_bytes=len(pcm),
                        duration_ms=round(duration_ms, 1),
                        text_tail=text_tail(text, 120),
                    )
                    continue

                t = (time.time() - t0) * 1000
                self.total_tts_ms += t
                self.sentence_count += 1
                if self._first_sentence_time is None:
                    self._first_sentence_time = time.time()
                log_tts_event(
                    "tts_synth_done",
                    sentence_id=sentence_id,
                    source=source,
                    voice=voice,
                    elapsed_ms=round(t, 1),
                    pcm_bytes=len(pcm),
                    duration_ms=round(duration_ms, 1),
                    text=text,
                    text_tail=text_tail(text, 160),
                )
                console_print(
                    "[tts.synth] event=sentence_done elapsed_ms={:.0f} voice={} text={}".format(
                        t,
                        voice,
                        _console_text(text),
                    ),
                    flush=True,
                    defer_during_stream=True,
                )
                self._audio_queue.put(("pcm", pcm, sentence_id))
                log_tts_event(
                    "tts_audio_enqueue",
                    sentence_id=sentence_id,
                    item_type="pcm",
                    audio_queue_size=self._audio_queue.qsize(),
                    pcm_bytes=len(pcm),
                    duration_ms=round(duration_ms, 1),
                )
                # 句间静音
                self._audio_queue.put(("silence", None, sentence_id))
                log_tts_event(
                    "tts_audio_enqueue",
                    sentence_id=sentence_id,
                    item_type="silence",
                    audio_queue_size=self._audio_queue.qsize(),
                    pcm_bytes=len(_SILENCE_BYTES),
                    duration_ms=TTS_REALTIME_SILENCE_MS,
                )
            except Exception as e:
                sentence_id = None
                text = ""
                if isinstance(item, tuple):
                    if len(item) == 4:
                        sentence_id, _voice, text, _source = item
                    elif len(item) == 2:
                        _voice, text = item
                log_tts_event(
                    "tts_synth_error",
                    sentence_id=sentence_id,
                    error=type(e).__name__,
                    message=str(e).replace("\n", " ")[:500],
                    text_tail=text_tail(text, 160),
                )
                console_print(
                    "[tts.synth] event=sentence_error error={}".format(
                        str(e).replace("\n", " ")[:220]
                    ),
                    flush=True,
                )
            finally:
                self._sentence_queue.task_done()

    # ── 播放线程 ────────────────────────────────────────────

    def _play_pcm(self, pcm_data: bytes, sentence_id=None, item_type: str = "pcm"):
        """通过 aplay stdin 播放一段 24kHz PCM，阻塞至播放完成"""
        t0 = time.time()
        returncode = None
        error = ""
        # AEC 参考信号：重采样 24kHz→16kHz → 临时 WAV → feed_wav
        if self._aec and pcm_data:
            pcm_16k = _resample_pcm(pcm_data, 24000, 16000)
            wav_path = _pcm_to_wav(pcm_16k, 16000)
            try:
                self._aec.feed_wav(wav_path)
            except Exception as e:
                console_print("[tts] event=aec_feed_error error={}".format(e), flush=True, defer_during_stream=True)
            finally:
                try:
                    os.remove(wav_path)
                except Exception:
                    pass

        # 播放
        proc = None
        try:
            with self._player_lock:
                self._player_proc = subprocess.Popen(
                    ["aplay", "-q", "-D", DEVICE_SPK,
                     "-f", "S16_LE", "-r", "24000", "-c", "1"],
                    stdin=subprocess.PIPE,
                )
                proc = self._player_proc
            proc.stdin.write(pcm_data)
            proc.stdin.close()
            proc.wait(timeout=60)
            returncode = proc.returncode
        except Exception as exc:
            error = "{}:{}".format(type(exc).__name__, str(exc).replace("\n", " ")[:220])
        finally:
            with self._player_lock:
                if self._player_proc is not None:
                    try:
                        if self._player_proc.poll() is None:
                            self._player_proc.kill()
                    except Exception:
                        pass
                    self._player_proc = None
            log_tts_event(
                "tts_play_pcm_done",
                sentence_id=sentence_id,
                item_type=item_type,
                pcm_bytes=len(pcm_data or b""),
                duration_ms=round(len(pcm_data or b"") / max(1, TTS_REALTIME_SAMPLE_RATE * 2) * 1000, 1),
                elapsed_ms=round((time.time() - t0) * 1000, 1),
                returncode=returncode,
                error=error,
            )

    def _play_wav(self, filepath: str, sentence_id=None):
        """播放预生成 WAV 文件（兼容 fillers.py 的 queue_wav）"""
        t0 = time.time()
        returncode = None
        error = ""
        play_path = _amplify_wav(filepath)
        if self._aec:
            try:
                self._aec.feed_wav(play_path)
            except Exception as e:
                console_print("[tts] event=aec_feed_error path={} error={}".format(play_path, e), flush=True, defer_during_stream=True)

        proc = None
        try:
            with self._player_lock:
                self._player_proc = subprocess.Popen(
                    ["aplay", "-q", "-D", DEVICE_SPK, play_path],
                )
                proc = self._player_proc
            proc.wait(timeout=60)
            returncode = proc.returncode
        except Exception as exc:
            error = "{}:{}".format(type(exc).__name__, str(exc).replace("\n", " ")[:220])
        finally:
            with self._player_lock:
                if self._player_proc is not None:
                    try:
                        if self._player_proc.poll() is None:
                            self._player_proc.kill()
                    except Exception:
                        pass
                    self._player_proc = None
            if play_path != filepath:
                try:
                    os.remove(play_path)
                except Exception:
                    pass
            log_tts_event(
                "tts_play_wav_done",
                sentence_id=sentence_id,
                path=filepath,
                play_path=play_path,
                elapsed_ms=round((time.time() - t0) * 1000, 1),
                returncode=returncode,
                error=error,
            )

    def _play_loop(self):
        """播放线程：处理 ("pcm",bytes) | ("silence",None) | ("wav",path) | None"""
        while True:
            item = self._audio_queue.get()
            try:
                if item is None:
                    continue

                if isinstance(item, tuple) and len(item) == 3:
                    item_type, data, sentence_id = item
                else:
                    item_type, data = item
                    sentence_id = None

                if item_type == "silence":
                    self._play_pcm(_SILENCE_BYTES, sentence_id=sentence_id, item_type="silence")
                    continue

                if item_type == "pcm":
                    self._play_pcm(data, sentence_id=sentence_id, item_type="pcm")
                    continue

                if item_type == "wav":
                    self._play_wav(data, sentence_id=sentence_id)
                    continue
            finally:
                self._audio_queue.task_done()

    # ── 控制接口 ────────────────────────────────────────────

    def cancel(self):
        """打断：停止播放、清空队列、断开 WebSocket"""
        log_tts_event(
            "tts_cancel",
            sentence_queue_size=self._sentence_queue.qsize(),
            audio_queue_size=self._audio_queue.qsize(),
            buffer_tail=text_tail(self.buffer, 160),
        )
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
        if self._session:
            self._session.cancel()
            self._session = None
            self._session_voice = None

    def reset(self):
        """重置取消标志，恢复正常"""
        self._cancel_flag.clear()
        self.buffer = ""
        self._in_paren = False
        self._active_voice = self.voice

    def flush(self):
        """强制输出 buffer 中剩余内容"""
        if self.buffer.strip():
            log_tts_event(
                "tts_flush_buffer",
                voice=self._active_voice,
                text=self.buffer.strip(),
                text_tail=text_tail(self.buffer.strip(), 160),
            )
            self._queue_sentence(self._active_voice, self.buffer.strip(), "flush")
            self.buffer = ""

    def queue_wav(self, filepath: str):
        """将预生成的 WAV 文件压入播放队列（兼容 fillers）"""
        log_tts_event(
            "tts_audio_enqueue",
            sentence_id=None,
            item_type="wav",
            path=filepath,
            audio_queue_size=self._audio_queue.qsize(),
        )
        self._audio_queue.put(("wav", filepath, None))

    def queue_phrase(self, phrase_id: str, text: str, voice: str = None,
                     fallback_wav: str = None) -> bool:
        """Queue a fixed phrase through the global generated-WAV cache."""
        text = (text or "").strip()
        if not text:
            return False
        voice = voice or self.voice
        instructions = _SPEED_INSTRUCTIONS.get(self.speed, "")
        key = PhraseCacheKey(
            phrase_id=phrase_id,
            text=text,
            voice=voice,
            model=REALTIME_TTS_MODEL,
            instructions=instructions,
            sample_rate=TTS_REALTIME_SAMPLE_RATE,
        )

        try:
            path, cached = get_or_create_wav(
                key,
                lambda: self._synthesize_phrase_pcm(voice, instructions, text),
            )
            console_print(
                "[tts.phrase] event=ready source={} voice={} phrase_id={} text={}".format(
                    "cache" if cached else "generated",
                    voice,
                    phrase_id,
                    _console_text(text),
                ),
                flush=True,
                defer_during_stream=True,
            )
            self.queue_wav(str(path))
            return True
        except Exception as exc:
            console_print(
                "[tts.phrase] event=error phrase_id={} error={}".format(
                    phrase_id,
                    str(exc).replace("\n", " ")[:220],
                ),
                flush=True,
            )
            if fallback_wav and os.path.exists(fallback_wav):
                self.queue_wav(fallback_wav)
                return False
            self.feed(text)
            self.flush()
            return False

    def _synthesize_phrase_pcm(self, voice: str, instructions: str, text: str) -> bytes:
        with self._phrase_lock:
            if (
                self._phrase_session is None
                or voice != self._phrase_session_voice
                or instructions != self._phrase_session_instructions
            ):
                if self._phrase_session:
                    try:
                        self._phrase_session.close()
                    except Exception:
                        pass
                self._phrase_session = RealtimeTTSSession(voice=voice, instructions=instructions)
                self._phrase_session_voice = voice
                self._phrase_session_instructions = instructions
            pcm = self._phrase_session.synthesize(text)
            return _normalize_volume(pcm)

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
