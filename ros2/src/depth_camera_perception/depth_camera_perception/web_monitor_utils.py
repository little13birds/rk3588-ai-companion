from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Optional, Tuple

BgrColor = Tuple[int, int, int]
BGR_GREEN: BgrColor = (0, 220, 80)
BGR_RED: BgrColor = (30, 30, 255)
BGR_GRAY: BgrColor = (180, 180, 180)


class FpsCounter:
    def __init__(self, window_sec: float = 5.0):
        self._window_sec = float(window_sec)
        self._samples: List[float] = []

    def mark(self, timestamp_s: float) -> None:
        timestamp = float(timestamp_s)
        self._samples.append(timestamp)
        self._trim(timestamp)

    def fps(self, now_s: Optional[float] = None) -> float:
        if not self._samples:
            return 0.0
        now = float(now_s) if now_s is not None else self._samples[-1]
        self._trim(now)
        if not self._samples:
            return 0.0
        if len(self._samples) == 1:
            return 1.0
        elapsed = max(1e-6, self._samples[-1] - self._samples[0])
        return float((len(self._samples) - 1) / elapsed)

    def _trim(self, now_s: float) -> None:
        cutoff = now_s - self._window_sec
        self._samples = [ts for ts in self._samples if ts >= cutoff]


def choose_box_color(is_fast: bool, has_depth: bool = True) -> BgrColor:
    if not has_depth:
        return BGR_GRAY
    return BGR_RED if is_fast else BGR_GREEN


@dataclass(frozen=True)
class MonitorStatus:
    camera_fps: float
    inference_fps: float
    stream_fps: float
    image_width: int
    image_height: int
    person_count: int
    nearest_distance_m: Optional[float]
    nearest_speed_mps: Optional[float]
    speed_threshold_mps: float
    fast_active: bool
    alert_active: bool
    last_update_s: float
    message: str

    def to_dict(self) -> dict:
        return {
            "fps": {
                "camera": round(self.camera_fps, 2),
                "inference": round(self.inference_fps, 2),
                "stream": round(self.stream_fps, 2),
            },
            "image": {
                "width": self.image_width,
                "height": self.image_height,
            },
            "people": {
                "count": self.person_count,
                "nearest_distance_m": None
                if self.nearest_distance_m is None
                else round(self.nearest_distance_m, 3),
                "nearest_speed_mps": None
                if self.nearest_speed_mps is None
                else round(self.nearest_speed_mps, 3),
            },
            "alert": {
                "active": self.alert_active,
                "fast_active": self.fast_active,
                "threshold_mps": self.speed_threshold_mps,
                "message": self.message,
            },
            "last_update_s": round(self.last_update_s, 3),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)
