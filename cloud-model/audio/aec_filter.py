"""线程安全 AEC 过滤器 — 基于 WebRTC AudioProcessing"""
import os, wave, threading
import numpy as np
from audio.echo_cancel import EchoCanceller

AEC_FRAME_SAMPLES = 160  # 10ms @ 16kHz


class SharedAecFilter:
    """线程安全的 AEC 过滤器，供主线程(mic处理)和播放线程(参考信号)共享"""

    def __init__(self, sample_rate: int = 16000, channels: int = 1):
        self._aec = EchoCanceller(sample_rate, channels, AEC_FRAME_SAMPLES)
        self._lock = threading.Lock()
        self._ref_fed_frames = 0
        self._mic_processed_frames = 0

    def feed_wav(self, wav_path: str):
        """读取 WAV 文件 PCM 数据，按 10ms 帧喂入 AEC 参考信号"""
        try:
            with wave.open(wav_path, 'rb') as wf:
                pcm = wf.readframes(wf.getnframes())
        except Exception:
            # 回退：尝试跳过44字节头部
            try:
                with open(wav_path, 'rb') as f:
                    data = f.read()
                pcm = data[44:]  # 跳过标准 WAV 头
            except Exception as e:
                print("[aec] event=wav_read_failed path={} error={}".format(wav_path, e), flush=True)
                return

        if not pcm:
            return

        # 按 160 样本帧逐帧喂入，帧间释放锁避免阻塞主循环
        frame_bytes = AEC_FRAME_SAMPLES * 2  # 320 bytes per frame
        for offset in range(0, len(pcm) - frame_bytes + 1, frame_bytes):
            with self._lock:
                self._aec.feed_playback(pcm[offset:offset + frame_bytes])
                self._ref_fed_frames += 1

    def process_mic(self, pcm: bytes) -> bytes:
        """处理麦克风 PCM 数据，返回消除回声后的数据"""
        if len(pcm) != AEC_FRAME_SAMPLES * 2:
            return pcm  # 帧大小不匹配，原样返回
        with self._lock:
            self._mic_processed_frames += 1
            return self._aec.process(pcm)

    def reset(self):
        """重置 AEC 状态（打断后调用）"""
        with self._lock:
            self._aec.close()
            self._aec = EchoCanceller(16000, 1, AEC_FRAME_SAMPLES)
            self._ref_fed_frames = 0
            self._mic_processed_frames = 0

    def stats(self):
        return {
            "ref_frames": self._ref_fed_frames,
            "mic_frames": self._mic_processed_frames,
        }
