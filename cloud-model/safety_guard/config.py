"""Configuration for the cloud-model safety guard."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _path_env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _expand(path: str) -> Path:
    return Path(path).expanduser()


@dataclass
class SafetyGuardConfig:
    enabled: bool = True
    fail_open: bool = True
    target_frequency_hz: float = 20.0
    hazard_period_sec: float = 0.2
    rgb_topic: str = "/camera/color/image_raw"
    qos_depth: int = 4

    native_lib_path: str = ""
    model_dir: str = ""

    pose_conf: float = 0.35
    hand_conf: float = 0.30
    hazard_conf: float = 0.04
    relation_min_px: float = 48.0
    relation_diag_ratio: float = 0.06
    overlay_scale: float = 0.38
    show_top_status: bool = True
    jpeg_quality: int = 72
    output_width: int = 960
    output_height: int = 720

    event_cooldown_sec: float = 8.0
    analyzer_max_pending: int = 3
    analyzer_timeout_sec: float = 15.0
    record_dir: str = "~/cloud-model/safety_records"
    announce_min_severity: str = "medium"
    announce_cooldown_sec: float = 20.0

    @classmethod
    def from_env(cls) -> "SafetyGuardConfig":
        return cls(
            enabled=_bool_env("SAFETY_GUARD_ENABLED", True),
            fail_open=_bool_env("SAFETY_GUARD_FAIL_OPEN", True),
            target_frequency_hz=_float_env("SAFETY_GUARD_TARGET_HZ", 20.0),
            hazard_period_sec=_float_env("SAFETY_GUARD_HAZARD_PERIOD", 0.2),
            rgb_topic=os.environ.get("SAFETY_GUARD_RGB_TOPIC", "/camera/color/image_raw"),
            qos_depth=_int_env("SAFETY_GUARD_QOS_DEPTH", 4),
            native_lib_path=_path_env("SAFETY_GUARD_LIB", ""),
            model_dir=_path_env("SAFETY_GUARD_MODEL_DIR", ""),
            pose_conf=_float_env("SAFETY_GUARD_POSE_CONF", 0.35),
            hand_conf=_float_env("SAFETY_GUARD_HAND_CONF", 0.30),
            hazard_conf=_float_env("SAFETY_GUARD_HAZARD_CONF", 0.04),
            relation_min_px=_float_env("SAFETY_GUARD_RELATION_MIN_PX", 48.0),
            relation_diag_ratio=_float_env("SAFETY_GUARD_RELATION_DIAG_RATIO", 0.06),
            overlay_scale=_float_env("SAFETY_GUARD_OVERLAY_SCALE", 0.38),
            show_top_status=_bool_env("SAFETY_GUARD_SHOW_TOP_STATUS", True),
            jpeg_quality=_int_env("SAFETY_GUARD_JPEG_QUALITY", 72),
            output_width=_int_env("SAFETY_GUARD_OUTPUT_WIDTH", 960),
            output_height=_int_env("SAFETY_GUARD_OUTPUT_HEIGHT", 720),
            event_cooldown_sec=_float_env("SAFETY_GUARD_EVENT_COOLDOWN", 8.0),
            analyzer_max_pending=_int_env("SAFETY_GUARD_ANALYZER_MAX_PENDING", 3),
            analyzer_timeout_sec=_float_env("SAFETY_GUARD_ANALYZER_TIMEOUT", 15.0),
            record_dir=_path_env("SAFETY_GUARD_RECORD_DIR", "~/cloud-model/safety_records"),
            announce_min_severity=os.environ.get("SAFETY_GUARD_ANNOUNCE_MIN_SEVERITY", "medium"),
            announce_cooldown_sec=_float_env("SAFETY_GUARD_ANNOUNCE_COOLDOWN", 20.0),
        )

    @property
    def record_path(self) -> Path:
        return _expand(self.record_dir)

    def resolve_native_lib(self) -> Path:
        candidates = []
        if self.native_lib_path:
            candidates.append(_expand(self.native_lib_path))
        candidates.extend([
            Path("~/cloud-model/safety_guard_native/build/libsafety_rknn.so").expanduser(),
            Path("~/safety_guard_rk3588/board/build/libsafety_rknn.so").expanduser(),
        ])
        for path in candidates:
            if path.exists():
                return path
        return candidates[0]

    def resolve_model_dir(self) -> Path:
        candidates = []
        if self.model_dir:
            candidates.append(_expand(self.model_dir))
        candidates.extend([
            Path("~/cloud-model/safety_guard/models").expanduser(),
            Path("~/safety_guard_rk3588/model").expanduser(),
        ])
        for path in candidates:
            if path.exists():
                return path
        return candidates[0]

    def clamp(self) -> "SafetyGuardConfig":
        self.target_frequency_hz = max(0.2, min(float(self.target_frequency_hz), 20.0))
        self.hazard_period_sec = max(0.05, min(float(self.hazard_period_sec), 5.0))
        self.event_cooldown_sec = max(1.0, min(float(self.event_cooldown_sec), 120.0))
        self.announce_cooldown_sec = max(0.0, min(float(self.announce_cooldown_sec), 300.0))
        self.analyzer_max_pending = max(1, min(int(self.analyzer_max_pending), 20))
        return self
