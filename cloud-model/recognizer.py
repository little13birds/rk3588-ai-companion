"""本地 ASR — SenseVoice + VAD + KWS 唤醒词"""
import sys, os, threading, queue
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sherpa_onnx
from config import SAMPLE_RATE

MODEL_DIR = "/home/elf/Desktop/reconstruct/model/sensevoice"
ASR_MODEL = os.path.join(MODEL_DIR, "model.int8.onnx")
ASR_TOKENS = os.path.join(MODEL_DIR, "tokens.txt")
VAD_MODEL = os.path.join(MODEL_DIR, "silero_vad.onnx")
KWS_ENCODER = os.path.join(MODEL_DIR, "kws/encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx")
KWS_DECODER = os.path.join(MODEL_DIR, "kws/decoder-epoch-12-avg-2-chunk-16-left-64.onnx")
KWS_JOINER = os.path.join(MODEL_DIR, "kws/joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx")
KWS_TOKENS = os.path.join(MODEL_DIR, "kws/tokens.txt")
KWS_KEYWORDS = os.path.join(MODEL_DIR, "kws/keywords.txt")


class ASRProcessor:
    """本地语音识别：SenseVoice ASR + VAD 断句 + 唤醒词检测"""

    def __init__(self, silence_timeout_ms: float = 800,
                 on_result=None, on_wake=None, on_vad=None):
        self.on_result = on_result
        self.on_wake = on_wake
        self.on_vad = on_vad

        self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=ASR_MODEL, tokens=ASR_TOKENS,
            num_threads=4, use_itn=True, language="auto",
        )

        vad_cfg = sherpa_onnx.VadModelConfig()
        vad_cfg.silero_vad.model = VAD_MODEL
        vad_cfg.silero_vad.threshold = 0.15
        vad_cfg.silero_vad.min_speech_duration = 0.15
        vad_cfg.silero_vad.max_speech_duration = 15.0
        vad_cfg.silero_vad.min_silence_duration = silence_timeout_ms / 1000.0
        vad_cfg.silero_vad.window_size = 512
        vad_cfg.sample_rate = SAMPLE_RATE
        vad_cfg.num_threads = 1
        self.vad = sherpa_onnx.VoiceActivityDetector(vad_cfg,
                                                      buffer_size_in_seconds=30)

        self.kws = sherpa_onnx.KeywordSpotter(
            encoder=KWS_ENCODER, decoder=KWS_DECODER, joiner=KWS_JOINER,
            tokens=KWS_TOKENS, keywords_file=KWS_KEYWORDS,
            num_threads=1, max_active_paths=4,
            keywords_threshold=0.1, keywords_score=3.0,
        )
        self.kws_stream = self.kws.create_stream()

        self.is_awake = False
        self._vad_speaking = False
        self._audio_queue = queue.Queue()
        self._running = False
        self._worker = None

    def start(self):
        self._running = True
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def stop(self):
        self._running = False
        self._audio_queue.put(b"")
        if self._worker:
            self._worker.join(timeout=3)

    def feed(self, samples):
        if self._running:
            self._audio_queue.put(samples)

    def sleep(self):
        self._set_awake(False)

    def force_awake(self):
        """打断专用：强制进入干净 AWAKE 状态，丢弃所有中间态"""
        if self.is_awake:
            self._set_awake(False)
        self._set_awake(True)

    def _set_awake(self, state: bool):
        if state == self.is_awake:
            return
        self.is_awake = state
        print("[状态] {}".format("唤醒" if state else "休眠"), flush=True)
        if self._vad_speaking and self.on_vad:
            self.on_vad(False)
        self._vad_speaking = False
        self.vad.reset()
        self.kws_stream = self.kws.create_stream()

    def _run(self):
        self._kws_chunks = 0
        self._vad_chunks = 0
        while self._running:
            item = self._audio_queue.get()
            if isinstance(item, bytes) or not item:
                continue

            if not self.is_awake:
                # 【休眠态】只跑 KWS 监听唤醒词
                self.kws_stream.accept_waveform(SAMPLE_RATE, item)
                while self.kws.is_ready(self.kws_stream):
                    self.kws.decode_stream(self.kws_stream)
                kw = self.kws.get_result(self.kws_stream)
                self._kws_chunks += 1
                if self._kws_chunks % 50 == 0:
                    print("[KWS] 已处理{}块, qsize={}".format(
                        self._kws_chunks, self._audio_queue.qsize()), flush=True)
                if kw:
                    print("[KWS] 检测到: {}".format(kw), flush=True)
                    if self.on_wake:
                        self.on_wake(kw)
                    self._set_awake(True)
            else:
                # 【唤醒态】VAD + ASR
                self.vad.accept_waveform(item)

                speaking = self.vad.is_speech_detected()
                if not hasattr(self, "_awake_chunks"):
                    self._awake_chunks = 0
                self._awake_chunks += 1
                if self._awake_chunks % 100 == 0:
                    print("[AWAKE] {} chunks, speaking={}"
                          .format(self._awake_chunks, speaking), flush=True)

                if speaking != self._vad_speaking:
                    self._vad_speaking = speaking
                    print("[VAD] {}".format("说话中" if speaking else "静音"), flush=True)
                    if self.on_vad:
                        self.on_vad(speaking)

                while not self.vad.empty():
                    if self._vad_speaking and self.on_vad:
                        self.on_vad(False)
                        self._vad_speaking = False
                    seg = self.vad.front
                    stream = self.recognizer.create_stream()
                    stream.accept_waveform(SAMPLE_RATE, seg.samples)
                    self.recognizer.decode_stream(stream)
                    if stream.result and stream.result.text.strip():
                        r = stream.result
                        self._fire_result(
                            r.text.strip(),
                            getattr(r, "emotion", "unknown"),
                            getattr(r, "event", "unknown"))
                    self.vad.pop()

    def _fire_result(self, text, emotion, event):
        if self.on_result:
            self.on_result({"text": text, "emotion": emotion,
                           "event": event})
