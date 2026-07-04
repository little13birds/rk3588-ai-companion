from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Optional, Tuple

BBox = Tuple[float, float, float, float]
XYZ = Tuple[float, float, float]


@dataclass(frozen=True)
class CameraModel:
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float

    @classmethod
    def approximate(cls, width: int, height: int) -> "CameraModel":
        focal = float(max(width, height))
        return cls(width=width, height=height, fx=focal, fy=focal, cx=width / 2.0, cy=height / 2.0)


@dataclass(frozen=True)
class PersonObservation:
    timestamp_s: float
    bbox: BBox
    confidence: float
    distance_m: float
    camera: CameraModel
    track_id: Optional[int] = None


@dataclass(frozen=True)
class SpeedUpdate:
    timestamp_s: float
    speed_mps: Optional[float]
    over_threshold_duration_s: float
    alert_triggered: bool
    alert_event_json: Optional[str] = None


def project_bbox_to_camera_xyz(bbox: BBox, distance_m: float, camera: CameraModel) -> XYZ:
    x1, y1, x2, y2 = bbox
    center_x = (x1 + x2) / 2.0
    center_y = (y1 + y2) / 2.0
    z = float(distance_m)
    x = (center_x - camera.cx) * z / camera.fx
    y = (center_y - camera.cy) * z / camera.fy
    return x, y, z


def _distance_3d(a: XYZ, b: XYZ) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


class PersonSpeedMonitor:
    def __init__(
        self,
        speed_threshold_mps: float = 1.5,
        duration_threshold_s: float = 1.0,
        alert_cooldown_s: float = 5.0,
        max_sample_gap_s: float = 0.75,
    ):
        self._speed_threshold_mps = float(speed_threshold_mps)
        self._duration_threshold_s = float(duration_threshold_s)
        self._alert_cooldown_s = float(alert_cooldown_s)
        self._max_sample_gap_s = float(max_sample_gap_s)
        self._last_timestamp_s: Optional[float] = None
        self._last_position: Optional[XYZ] = None
        self._over_threshold_since_s: Optional[float] = None
        self._last_alert_s: Optional[float] = None

    def reset(self) -> None:
        self._last_timestamp_s = None
        self._last_position = None
        self._over_threshold_since_s = None

    def update(self, observation: PersonObservation) -> SpeedUpdate:
        position = project_bbox_to_camera_xyz(
            observation.bbox,
            distance_m=observation.distance_m,
            camera=observation.camera,
        )
        if self._last_timestamp_s is None or self._last_position is None:
            self._last_timestamp_s = observation.timestamp_s
            self._last_position = position
            return SpeedUpdate(observation.timestamp_s, None, 0.0, False)

        dt = observation.timestamp_s - self._last_timestamp_s
        if dt <= 0.0 or dt > self._max_sample_gap_s:
            self.reset()
            self._last_timestamp_s = observation.timestamp_s
            self._last_position = position
            return SpeedUpdate(observation.timestamp_s, None, 0.0, False)

        speed_mps = _distance_3d(position, self._last_position) / dt
        self._last_timestamp_s = observation.timestamp_s
        self._last_position = position

        if speed_mps > self._speed_threshold_mps:
            if self._over_threshold_since_s is None:
                self._over_threshold_since_s = observation.timestamp_s - dt
        else:
            self._over_threshold_since_s = None

        over_duration = 0.0
        if self._over_threshold_since_s is not None:
            over_duration = observation.timestamp_s - self._over_threshold_since_s

        cooldown_ready = (
            self._last_alert_s is None
            or observation.timestamp_s - self._last_alert_s >= self._alert_cooldown_s
        )
        should_alert = over_duration >= self._duration_threshold_s and cooldown_ready
        event_json = None
        if should_alert:
            self._last_alert_s = observation.timestamp_s
            event_json = self._build_alert_event_json(observation, speed_mps, over_duration)

        return SpeedUpdate(
            timestamp_s=observation.timestamp_s,
            speed_mps=speed_mps,
            over_threshold_duration_s=over_duration,
            alert_triggered=should_alert,
            alert_event_json=event_json,
        )

    def _build_alert_event_json(
        self,
        observation: PersonObservation,
        speed_mps: float,
        over_duration_s: float,
    ) -> str:
        payload = {
            "event": "person_speed_alert",
            "target_type": "person",
            "timestamp_s": round(observation.timestamp_s, 3),
            "speed_mps": round(speed_mps, 3),
            "threshold_mps": self._speed_threshold_mps,
            "over_threshold_duration_s": round(over_duration_s, 3),
            "distance_m": round(observation.distance_m, 3),
            "confidence": round(observation.confidence, 3),
            "track_id": observation.track_id,
            "message": "person moving too fast",
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
