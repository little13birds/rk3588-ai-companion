"""ctypes wrapper for libsafety_rknn.so."""
from __future__ import annotations

import ctypes
import json
from pathlib import Path
from typing import Tuple

import numpy as np

from .config import SafetyGuardConfig


class SafetyRknnRuntime:
    def __init__(self, config: SafetyGuardConfig):
        self.config = config.clamp()
        self.lib_path = self.config.resolve_native_lib()
        self.model_dir = self.config.resolve_model_dir()
        self._handle = None

        if not self.lib_path.exists():
            raise FileNotFoundError(f"safety native library not found: {self.lib_path}")

        self._lib = ctypes.CDLL(str(self.lib_path), mode=ctypes.RTLD_GLOBAL)
        self._setup_signatures()

        pose = self.model_dir / "pose_yolov8n_hybrid.rknn"
        hand = self.model_dir / "hand_yolov8n_int8.rknn"
        hazard = self.model_dir / "hazard_yolov8s_coco_int8.rknn"
        for model in (pose, hand, hazard):
            if not model.exists():
                raise FileNotFoundError(f"safety model not found: {model}")

        self._handle = self._lib.safety_rknn_create(
            str(pose).encode(),
            str(hand).encode(),
            str(hazard).encode(),
        )
        if not self._handle:
            raise RuntimeError(self.last_error() or "safety_rknn_create failed")

        self.configure(self.config)
        self._jpeg_cap = 4 * 1024 * 1024
        self._jpeg_buf = (ctypes.c_ubyte * self._jpeg_cap)()
        self._json_cap = 128 * 1024
        self._json_buf = ctypes.create_string_buffer(self._json_cap)

    def _setup_signatures(self) -> None:
        self._lib.safety_rknn_create.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
        self._lib.safety_rknn_create.restype = ctypes.c_void_p
        self._lib.safety_rknn_destroy.argtypes = [ctypes.c_void_p]
        self._lib.safety_rknn_destroy.restype = None
        self._lib.safety_rknn_set_config.argtypes = [
            ctypes.c_void_p,
            ctypes.c_float,
            ctypes.c_float,
            ctypes.c_float,
            ctypes.c_float,
            ctypes.c_float,
            ctypes.c_float,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._lib.safety_rknn_set_config.restype = ctypes.c_int
        self._lib.safety_rknn_process_bgr.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_double,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        self._lib.safety_rknn_process_bgr.restype = ctypes.c_int
        self._lib.safety_rknn_last_error.argtypes = [ctypes.c_void_p]
        self._lib.safety_rknn_last_error.restype = ctypes.c_char_p

    def configure(self, config: SafetyGuardConfig) -> None:
        ret = self._lib.safety_rknn_set_config(
            self._handle,
            float(config.pose_conf),
            float(config.hand_conf),
            float(config.hazard_conf),
            float(config.relation_min_px),
            float(config.relation_diag_ratio),
            float(config.overlay_scale),
            1 if config.show_top_status else 0,
            int(config.jpeg_quality),
            int(config.output_width),
            int(config.output_height),
        )
        if ret != 0:
            raise RuntimeError(f"safety_rknn_set_config failed: {self.last_error()}")

    def last_error(self) -> str:
        raw = self._lib.safety_rknn_last_error(self._handle)
        return raw.decode("utf-8", "replace") if raw else ""

    def process(self, bgr: np.ndarray, now_sec: float, run_hazard: bool) -> Tuple[bytes, dict]:
        if bgr.ndim != 3 or bgr.shape[2] != 3 or bgr.dtype != np.uint8:
            raise ValueError(f"expected uint8 HxWx3 BGR frame, got {bgr.shape} {bgr.dtype}")
        frame = np.ascontiguousarray(bgr)
        height, width = frame.shape[:2]
        jpg_size = ctypes.c_int(0)
        ret = self._lib.safety_rknn_process_bgr(
            self._handle,
            frame.ctypes.data_as(ctypes.POINTER(ctypes.c_ubyte)),
            width,
            height,
            int(frame.strides[0]),
            float(now_sec),
            1 if run_hazard else 0,
            self._jpeg_buf,
            self._jpeg_cap,
            ctypes.byref(jpg_size),
            self._json_buf,
            self._json_cap,
        )
        raw_json = self._json_buf.value.decode("utf-8", "replace")
        if ret != 0:
            raise RuntimeError(f"safety_rknn_process_bgr failed ret={ret}: {self.last_error() or raw_json}")
        return bytes(self._jpeg_buf[: jpg_size.value]), json.loads(raw_json)

    def close(self) -> None:
        if self._handle:
            self._lib.safety_rknn_destroy(self._handle)
            self._handle = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
