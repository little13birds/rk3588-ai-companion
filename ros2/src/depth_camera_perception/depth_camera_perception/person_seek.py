from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple

BBox = Tuple[float, float, float, float]


@dataclass(frozen=True)
class PersonTarget:
    bbox: BBox
    confidence: float
    distance_m: float
    image_width: int
    image_height: int


@dataclass(frozen=True)
class PersonSeekConfig:
    stop_distance_m: float = 0.8
    stop_tolerance_m: float = 0.05
    search_angular_z: float = 0.25
    search_max_yaw_rad: float = 2.0 * math.pi
    search_timeout_s: float = 30.0
    approach_max_forward_mps: float = 0.40
    approach_slow_forward_mps: float = 0.10
    slowdown_distance_m: float = 1.20
    approach_angular_gain: float = 0.8
    approach_max_angular_z: float = 0.25
    center_tolerance_fraction: float = 0.08
    target_lost_timeout_s: float = 0.50


@dataclass(frozen=True)
class SeekOutput:
    state: str
    reason: str
    linear_x: float = 0.0
    angular_z: float = 0.0
    target_distance_m: Optional[float] = None
    target_confidence: Optional[float] = None
    target_center_error: Optional[float] = None
    scan_yaw_rad: float = 0.0
    scan_elapsed_s: float = 0.0


class PersonSeekController:
    def __init__(self, config: Optional[PersonSeekConfig] = None):
        self.config = config or PersonSeekConfig()
        self.state = "IDLE"
        self._search_start_s: Optional[float] = None
        self._last_yaw_rad: Optional[float] = None
        self._scan_yaw_rad = 0.0
        self._last_target_s: Optional[float] = None

    def start(self, now_s: float, yaw_rad: Optional[float] = None) -> None:
        self.state = "SEARCH_ROTATE"
        self._search_start_s = float(now_s)
        self._last_yaw_rad = yaw_rad
        self._scan_yaw_rad = 0.0
        self._last_target_s = None

    def cancel(self) -> None:
        self.state = "IDLE"
        self._search_start_s = None
        self._last_yaw_rad = None
        self._scan_yaw_rad = 0.0
        self._last_target_s = None

    def update(
        self,
        target: Optional[PersonTarget],
        now_s: float,
        yaw_rad: Optional[float] = None,
    ) -> SeekOutput:
        now_s = float(now_s)
        if self.state == "IDLE":
            return self._stop("idle", now_s)

        self._update_scan_yaw(yaw_rad)

        if self.state == "SEARCH_ROTATE":
            if _valid_target(target):
                self.state = "APPROACH"
                self._last_target_s = now_s
                return self._approach(target, now_s)
            if self._search_timed_out(now_s):
                self.state = "SEARCH_FAILED"
                return self._stop("search_timeout_no_target", now_s)
            if self._scan_yaw_rad >= self.config.search_max_yaw_rad:
                self.state = "SEARCH_FAILED"
                return self._stop("search_complete_no_target", now_s)
            return SeekOutput(
                state=self.state,
                reason="searching",
                angular_z=self.config.search_angular_z,
                scan_yaw_rad=self._scan_yaw_rad,
                scan_elapsed_s=self._elapsed(now_s),
            )

        if self.state == "APPROACH":
            if not _valid_target(target):
                if self._target_lost_timed_out(now_s):
                    self._restart_search(now_s)
                    return self._search_output("searching_after_target_lost", now_s)
                return self._stop("target_temporarily_lost", now_s)
            self._last_target_s = now_s
            return self._approach(target, now_s)

        if self.state == "TARGET_LOST" and _valid_target(target):
            self.state = "APPROACH"
            self._last_target_s = now_s
            return self._approach(target, now_s)

        return self._stop(_terminal_reason(self.state), now_s)

    def _approach(self, target: PersonTarget, now_s: float) -> SeekOutput:
        center_error = _target_center_error(target)
        if target.distance_m <= self.config.stop_distance_m + self.config.stop_tolerance_m:
            self.state = "ARRIVED"
            return SeekOutput(
                state=self.state,
                reason="arrived",
                target_distance_m=target.distance_m,
                target_confidence=target.confidence,
                target_center_error=center_error,
                scan_yaw_rad=self._scan_yaw_rad,
                scan_elapsed_s=self._elapsed(now_s),
            )

        angular_z = 0.0
        if abs(center_error) >= self.config.center_tolerance_fraction:
            angular_z = _clamp(
                -center_error * self.config.approach_angular_gain,
                -self.config.approach_max_angular_z,
                self.config.approach_max_angular_z,
            )

        linear_x = self.config.approach_max_forward_mps
        if target.distance_m <= self.config.slowdown_distance_m:
            linear_x = self.config.approach_slow_forward_mps

        return SeekOutput(
            state=self.state,
            reason="approaching_target",
            linear_x=linear_x,
            angular_z=angular_z,
            target_distance_m=target.distance_m,
            target_confidence=target.confidence,
            target_center_error=center_error,
            scan_yaw_rad=self._scan_yaw_rad,
            scan_elapsed_s=self._elapsed(now_s),
        )

    def _stop(self, reason: str, now_s: float) -> SeekOutput:
        return SeekOutput(
            state=self.state,
            reason=reason,
            scan_yaw_rad=self._scan_yaw_rad,
            scan_elapsed_s=self._elapsed(now_s),
        )

    def _update_scan_yaw(self, yaw_rad: Optional[float]) -> None:
        if yaw_rad is None:
            return
        if self._last_yaw_rad is None:
            self._last_yaw_rad = yaw_rad
            return
        delta = wrap_angle(yaw_rad - self._last_yaw_rad)
        self._scan_yaw_rad += abs(delta)
        self._last_yaw_rad = yaw_rad

    def _elapsed(self, now_s: float) -> float:
        if self._search_start_s is None:
            return 0.0
        return max(0.0, now_s - self._search_start_s)

    def _search_timed_out(self, now_s: float) -> bool:
        return self._elapsed(now_s) >= self.config.search_timeout_s

    def _target_lost_timed_out(self, now_s: float) -> bool:
        if self._last_target_s is None:
            return True
        return now_s - self._last_target_s >= self.config.target_lost_timeout_s

    def _restart_search(self, now_s: float) -> None:
        self.state = "SEARCH_ROTATE"
        self._search_start_s = float(now_s)
        self._scan_yaw_rad = 0.0
        self._last_target_s = None

    def _search_output(self, reason: str, now_s: float) -> SeekOutput:
        return SeekOutput(
            state=self.state,
            reason=reason,
            angular_z=self.config.search_angular_z,
            scan_yaw_rad=self._scan_yaw_rad,
            scan_elapsed_s=self._elapsed(now_s),
        )


def wrap_angle(angle_rad: float) -> float:
    while angle_rad > math.pi:
        angle_rad -= 2.0 * math.pi
    while angle_rad < -math.pi:
        angle_rad += 2.0 * math.pi
    return angle_rad


def _valid_target(target: Optional[PersonTarget]) -> bool:
    return target is not None and math.isfinite(target.distance_m) and target.distance_m > 0.0


def _target_center_error(target: PersonTarget) -> float:
    x1, _, x2, _ = target.bbox
    center_x = (x1 + x2) / 2.0
    image_center_x = float(target.image_width) / 2.0
    if image_center_x <= 0.0:
        return 0.0
    return _clamp((center_x - image_center_x) / image_center_x, -1.0, 1.0)


def _terminal_reason(state: str) -> str:
    if state == "ARRIVED":
        return "arrived"
    if state == "SEARCH_FAILED":
        return "search_failed"
    if state == "TARGET_LOST":
        return "target_lost"
    return "idle"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
