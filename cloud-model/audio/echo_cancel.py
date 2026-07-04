"""WebRTC 回声消除 — ctypes 封装"""
import os, ctypes
import numpy as np

_LIB = ctypes.CDLL(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "libaec_bridge.so"))

_LIB.aec_create.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
_LIB.aec_create.restype = ctypes.c_void_p

_LIB.aec_feed_playback.argtypes = [ctypes.c_void_p,
                                    ctypes.POINTER(ctypes.c_int16),
                                    ctypes.c_int]
_LIB.aec_feed_playback.restype = None

_LIB.aec_process.argtypes = [ctypes.c_void_p,
                              ctypes.POINTER(ctypes.c_int16),
                              ctypes.POINTER(ctypes.c_int16),
                              ctypes.c_int]
_LIB.aec_process.restype = ctypes.c_int

_LIB.aec_destroy.argtypes = [ctypes.c_void_p]
_LIB.aec_destroy.restype = None


class EchoCanceller:
    """WebRTC 回声消除器"""

    def __init__(self, sample_rate: int = 16000, channels: int = 1,
                 frame_size: int = 160):
        self.handle = _LIB.aec_create(sample_rate, channels, frame_size)
        if not self.handle:
            raise RuntimeError("AEC 初始化失败")

    def feed_playback(self, pcm: bytes):
        """喂入即将播放的音频（参考信号）"""
        if len(pcm) % 2 != 0:
            return
        samples = len(pcm) // 2
        arr = np.frombuffer(pcm, dtype=np.int16)
        ptr = arr.ctypes.data_as(ctypes.POINTER(ctypes.c_int16))
        _LIB.aec_feed_playback(self.handle, ptr, samples)

    def process(self, pcm: bytes) -> bytes:
        """处理麦克风音频，返回消除回声后的数据"""
        if len(pcm) % 2 != 0:
            return pcm
        samples = len(pcm) // 2
        inp = np.frombuffer(pcm, dtype=np.int16).copy()
        out = np.zeros(samples, dtype=np.int16)
        in_ptr = inp.ctypes.data_as(ctypes.POINTER(ctypes.c_int16))
        out_ptr = out.ctypes.data_as(ctypes.POINTER(ctypes.c_int16))
        _LIB.aec_process(self.handle, in_ptr, out_ptr, samples)
        return out.tobytes()

    def close(self):
        if self.handle:
            _LIB.aec_destroy(self.handle)
            self.handle = None

    def __del__(self):
        self.close()
