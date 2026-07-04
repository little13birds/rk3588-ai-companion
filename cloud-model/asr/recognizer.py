"""本地 ASR — SenseVoice + VAD + KWS 唤醒词（线程安全版）"""
import sys, os, threading, queue, numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sherpa_onnx
from config import SAMPLE_RATE
from asr.turn_detector import AwakeTurnDetector

MODEL_DIR = "/home/elf/Desktop/reconstruct/model/sensevoice"
ASR_MODEL = os.path.join(MODEL_DIR, "model.int8.onnx")
ASR_TOKENS = os.path.join(MODEL_DIR, "tokens.txt")
VAD_MODEL = os.path.join(MODEL_DIR, "silero_vad.onnx")
KWS_ENCODER = os.path.join(MODEL_DIR, "kws/encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx")
KWS_DECODER = os.path.join(MODEL_DIR, "kws/decoder-epoch-12-avg-2-chunk-16-left-64.onnx")
KWS_JOINER = os.path.join(MODEL_DIR, "kws/joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx")
KWS_TOKENS = os.path.join(MODEL_DIR, "kws/tokens.txt")
_REPO_KWS_KEYWORDS = os.path.join(os.path.dirname(__file__), "kws_keywords.txt")
KWS_KEYWORDS = os.environ.get(
    "ASR_KWS_KEYWORDS",
    _REPO_KWS_KEYWORDS if os.path.exists(_REPO_KWS_KEYWORDS)
    else os.path.join(MODEL_DIR, "kws/keywords.txt"),
)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


KWS_PROGRESS_EVERY = max(0, _int_env("ASR_KWS_PROGRESS_EVERY", 0))
AWAKE_PROGRESS_EVERY = max(0, _int_env("ASR_AWAKE_PROGRESS_EVERY", 0))
ASR_WAKE_PREROLL_MS = max(0, _int_env("ASR_WAKE_PREROLL_MS", 500))
ASR_MIN_SPEECH_MS = max(1, _int_env("ASR_MIN_SPEECH_MS", 200))
ASR_NO_SPEECH_SLEEP_MS = max(1, _int_env("ASR_NO_SPEECH_SLEEP_MS", 5000))
ASR_MAX_TURN_MS = max(1, _int_env("ASR_MAX_TURN_MS", 15000))


class ASRProcessor:
    """本地语音识别：SenseVoice ASR + VAD 断句 + 唤醒词检测
    所有 VAD/KWS 状态变更统一由 _run 线程执行，保证线程安全。
    """

    def __init__(self, silence_timeout_ms: float = 800,
                 on_result=None, on_wake=None, on_vad=None):
        self.on_result = on_result
        self.on_wake = on_wake
        self.on_vad = on_vad

        self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=ASR_MODEL, tokens=ASR_TOKENS,
            num_threads=2, use_itn=True, language="auto",
        )

        vad_cfg = sherpa_onnx.VadModelConfig()
        vad_cfg.silero_vad.model = VAD_MODEL
        vad_cfg.silero_vad.threshold = 0.7
        vad_cfg.silero_vad.min_speech_duration = 0.15
        vad_cfg.silero_vad.max_speech_duration = 15.0
        vad_cfg.silero_vad.min_silence_duration = silence_timeout_ms / 1000.0
        vad_cfg.silero_vad.window_size = 512
        vad_cfg.sample_rate = SAMPLE_RATE
        vad_cfg.num_threads = 1
        self.vad = sherpa_onnx.VoiceActivityDetector(vad_cfg,
                                                      buffer_size_in_seconds=30)
        self._turn_detector = AwakeTurnDetector(
            sample_rate=SAMPLE_RATE,
            preroll_ms=ASR_WAKE_PREROLL_MS,
            min_speech_ms=ASR_MIN_SPEECH_MS,
            trailing_silence_ms=int(silence_timeout_ms),
            no_speech_timeout_ms=ASR_NO_SPEECH_SLEEP_MS,
            max_turn_ms=ASR_MAX_TURN_MS,
        )

        self.kws = sherpa_onnx.KeywordSpotter(
            encoder=KWS_ENCODER, decoder=KWS_DECODER, joiner=KWS_JOINER,
            tokens=KWS_TOKENS, keywords_file=KWS_KEYWORDS,
            num_threads=1, max_active_paths=4,
            keywords_threshold=0.1, keywords_score=3.0,
        )
        self.kws_stream = self.kws.create_stream()

        self.is_awake = False
        self._vad_speaking = False
        self._audio_queue = queue.Queue(maxsize=300)
        self._running = False
        self._worker = None

        # 线程安全状态变更
        self._state_lock = threading.Lock()
        self._pending_state = None   # None=无变更, True=唤醒, False=休眠
        self._force_reset = False    # 打断专用：先休眠再唤醒的完整周期

        # 诊断计数
        self._kws_chunks = 0
        self._awake_chunks = 0

    def _reason_suffix(self, reason: str) -> str:
        return " reason={}".format(reason) if reason else ""

    def _log_mode_entry(self, awake: bool, reason: str = ""):
        if awake:
            message = "[ASR] enter AWAKE/VAD+ASR"
        else:
            message = "[ASR] enter SLEEP/KWS"
        print("{}{}".format(message, self._reason_suffix(reason)), flush=True)

    def _log_mode_exit(self, awake: bool, reason: str = ""):
        if awake:
            message = "[ASR] exit AWAKE/VAD+ASR"
        else:
            message = "[ASR] exit SLEEP/KWS"
        print("{}{}".format(message, self._reason_suffix(reason)), flush=True)

    def _set_vad_speaking(self, speaking: bool, reason: str = ""):
        if speaking == self._vad_speaking:
            return
        old = self._vad_speaking
        self._vad_speaking = speaking
        print("[AWAKE] speaking: {} -> {}{}".format(
            str(old).lower(),
            str(speaking).lower(),
            self._reason_suffix(reason),
        ), flush=True)
        if self.on_vad:
            self.on_vad(speaking)

    def is_speaking(self) -> bool:
        """Return whether the awake VAD currently sees active speech."""
        return bool(self._vad_speaking)

    def start(self):
        self._running = True
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def stop(self):
        self._running = False
        try:
            self._audio_queue.put_nowait(None)
        except queue.Full:
            self._audio_queue.put(None)
        if self._worker:
            self._worker.join(timeout=3)

    def feed(self, data):
        if self._running:
            try:
                if isinstance(data, (bytes, bytearray)):
                    self._audio_queue.put_nowait(data)
                else:
                    arr = np.array(data, dtype=np.float32) * 32768
                    self._audio_queue.put_nowait(arr.astype(np.int16).tobytes())
            except queue.Full:
                pass

    def sleep(self):
        """请求进入休眠态（主线程安全）"""
        self._request_state(False)

    def force_awake(self):
        """打断专用：请求完整重置后进入 AWAKE（主线程安全）"""
        with self._state_lock:
            self._force_reset = True
        self._request_state(True)

    def _request_state(self, state: bool):
        """Thread-safe: 请求状态变更，实际由 _run 线程执行"""
        with self._state_lock:
            if state == self.is_awake and not self._force_reset:
                return
            self._pending_state = state

    def _apply_state_change(self):
        """仅由 _run 线程调用：执行待处理的状态变更"""
        with self._state_lock:
            pending = self._pending_state
            force = self._force_reset
            self._pending_state = None
            self._force_reset = False

        if pending is None and not force:
            return

        if force:
            # 完整重置周期：SLEEP → AWAKE
            if self.is_awake:
                self._log_mode_exit(True, "force_reset")
                self._set_vad_speaking(False, "force_reset")
                self.is_awake = False
                self.vad.reset()
                self._turn_detector.reset()
                self.kws_stream = self.kws.create_stream()
                self._log_mode_entry(False, "force_reset")
            self._log_mode_exit(False, "force_reset")
            self.is_awake = True
            self.vad.reset()
            self._turn_detector.reset()
            self.kws_stream = self.kws.create_stream()
            self._awake_chunks = 0
            self._log_mode_entry(True, "force_reset")
        elif pending != self.is_awake:
            self._log_mode_exit(self.is_awake)
            self._set_vad_speaking(False, "mode_exit")
            self.is_awake = pending
            self.vad.reset()
            self._turn_detector.reset()
            self.kws_stream = self.kws.create_stream()
            self._awake_chunks = 0
            self._log_mode_entry(self.is_awake)

    def _decode_turn_samples(self, samples):
        stream = self.recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        self.recognizer.decode_stream(stream)
        if stream.result and stream.result.text.strip():
            r = stream.result
            self._fire_result(
                r.text.strip(),
                getattr(r, "emotion", "unknown"),
                getattr(r, "event", "unknown"))

    def _drain_vad_segments(self):
        while not self.vad.empty():
            self.vad.pop()

    def _run(self):
        self._kws_chunks = 0
        self._awake_chunks = 0
        self._log_mode_entry(self.is_awake, "initial")
        while self._running:
            # 先处理待执行的状态变更
            self._apply_state_change()

            item = self._audio_queue.get()
            if item is None or not item:
                continue

            samples = np.frombuffer(item, dtype=np.int16).astype(np.float32) / 32768.0
            samples = samples.tolist()

            if not self.is_awake:
                # 【休眠态】只跑 KWS 监听唤醒词
                self.kws_stream.accept_waveform(SAMPLE_RATE, samples)
                while self.kws.is_ready(self.kws_stream):
                    self.kws.decode_stream(self.kws_stream)
                kw = self.kws.get_result(self.kws_stream)
                self._kws_chunks += 1
                if KWS_PROGRESS_EVERY and self._kws_chunks % KWS_PROGRESS_EVERY == 0:
                    print("\r[KWS] chunks={} qsize={}".format(
                        self._kws_chunks, self._audio_queue.qsize()), end="", flush=True)
                if kw:
                    if KWS_PROGRESS_EVERY:
                        print("", flush=True)
                    print("[KWS] 检测到: {}".format(kw), flush=True)
                    if self.on_wake:
                        self.on_wake(kw)
            else:
                # 【唤醒态】VAD + ASR
                self.vad.accept_waveform(samples)
                self._awake_chunks += 1

                speaking = self.vad.is_speech_detected()
                if (AWAKE_PROGRESS_EVERY
                        and self._awake_chunks % AWAKE_PROGRESS_EVERY == 0):
                    print("\r[AWAKE] chunks={} speaking={} qsize={}".format(
                        self._awake_chunks, speaking,
                        self._audio_queue.qsize()), end="", flush=True)

                if speaking != self._vad_speaking:
                    if AWAKE_PROGRESS_EVERY:
                        print("", flush=True)
                    self._set_vad_speaking(speaking, "vad")

                decision = self._turn_detector.observe(samples, speaking)
                self._drain_vad_segments()

                if decision.kind == "ready":
                    self._set_vad_speaking(False, "turn_ready")
                    print("[AWAKE] turn=ready reason={} samples={}".format(
                        decision.reason, len(decision.samples or [])), flush=True)
                    self._decode_turn_samples(decision.samples or [])
                elif decision.kind == "discarded":
                    self._set_vad_speaking(False, "turn_discarded")
                    print("[AWAKE] turn=discarded reason={}".format(
                        decision.reason), flush=True)
                elif decision.kind == "timeout":
                    self._set_vad_speaking(False, "turn_timeout")
                    print("[AWAKE] turn=timeout reason={}".format(
                        decision.reason), flush=True)
                    self._request_state(False)

    def _fire_result(self, text, emotion, event):
        if self.on_result:
            self.on_result({"text": text, "emotion": emotion,
                           "event": event})
