"""Safety monitoring loop."""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict

import cv2

from .config import SafetyGuardConfig
from .rknn_runtime import SafetyRknnRuntime
from .ros_camera import RosRgbCamera
from .types import SafetyCandidate


class SafetyMonitor:
    def __init__(
        self,
        config: SafetyGuardConfig,
        runtime: SafetyRknnRuntime,
        camera: RosRgbCamera,
        on_candidate: Callable[[SafetyCandidate], None],
    ):
        self.config = config.clamp()
        self.runtime = runtime
        self.camera = camera
        self.on_candidate = on_candidate
        self._target_hz = self.config.target_frequency_hz
        self._target_lock = threading.Lock()
        self._pause_lock = threading.Lock()
        self._paused = False
        self._pause_reason = ""
        self._paused_since = 0.0
        self._stop = threading.Event()
        self._thread = None
        self._last_hazard_t = 0.0
        self._last_trigger: Dict[str, float] = {}
        self._active: Dict[str, bool] = {}
        self._frame_count = 0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="safety-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    def set_target_frequency_hz(self, hz: float) -> None:
        hz = max(0.2, min(float(hz), 20.0))
        with self._target_lock:
            self._target_hz = hz

    def get_target_frequency_hz(self) -> float:
        with self._target_lock:
            return self._target_hz

    def pause(self, reason: str = "") -> None:
        with self._pause_lock:
            if not self._paused:
                self._paused_since = time.monotonic()
            self._paused = True
            self._pause_reason = reason or "manual"
            self._active = {}

    def resume(self, reason: str = "") -> None:
        with self._pause_lock:
            self._paused = False
            self._pause_reason = ""
            self._paused_since = 0.0
            self._active = {}

    def is_paused(self) -> bool:
        with self._pause_lock:
            return self._paused

    def status(self) -> Dict[str, object]:
        with self._pause_lock:
            paused = self._paused
            pause_reason = self._pause_reason
            paused_since = self._paused_since
        return {
            "active": self._thread is not None and self._thread.is_alive(),
            "paused": paused,
            "pause_reason": pause_reason,
            "paused_sec": round(time.monotonic() - paused_since, 3) if paused and paused_since else 0.0,
            "target_hz": self.get_target_frequency_hz(),
            "frame_count": self._frame_count,
            "last_trigger": dict(self._last_trigger),
        }

    def _period(self) -> float:
        return 1.0 / max(0.2, self.get_target_frequency_hz())

    def _run(self) -> None:
        while not self._stop.is_set():
            start = time.monotonic()
            try:
                if self.is_paused():
                    time.sleep(min(self._period(), 0.2))
                    continue
                self._tick(start)
            except Exception as exc:
                print(f"[safety] event=tick_failed component=monitor error={exc}", flush=True)
            elapsed = time.monotonic() - start
            time.sleep(max(0.0, self._period() - elapsed))

    def _tick(self, now: float) -> None:
        if self.is_paused():
            return
        frame, _stamp = self.camera.latest_bgr()
        if frame is None:
            return

        run_hazard = now - self._last_hazard_t >= self.config.hazard_period_sec
        if run_hazard:
            self._last_hazard_t = now

        annotated_jpeg, status = self.runtime.process(frame, now, run_hazard)
        self._frame_count += 1

        candidate_type = self._candidate_type(status)
        if not candidate_type:
            for key in ("fall", "hazard"):
                self._active[key] = False
            return

        if not self._should_emit(candidate_type, now):
            return

        ok, raw = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            return
        candidate = SafetyCandidate.create(
            candidate_type=candidate_type,
            raw_jpeg=raw.tobytes(),
            annotated_jpeg=annotated_jpeg,
            rknn_status=status,
        )
        self.on_candidate(candidate)

    @staticmethod
    def _candidate_type(status: dict) -> str:
        fall = bool(status.get("fall_active"))
        hazard = bool(status.get("hand_hazard_active"))
        if fall and hazard:
            return "combined_candidate"
        if fall:
            return "fall_candidate"
        if hazard:
            return "hazard_candidate"
        return ""

    def _should_emit(self, candidate_type: str, now: float) -> bool:
        present = set(self._risk_families(candidate_type))
        for family in ("fall", "hazard"):
            if family not in present:
                self._active[family] = False

        due = []
        for family in present:
            was_active = self._active.get(family, False)
            last = self._last_trigger.get(family, -9999.0)
            if (not was_active) or (now - last) >= self.config.event_cooldown_sec:
                due.append(family)

        for family in present:
            self._active[family] = True
        for family in due:
            self._last_trigger[family] = now
        return bool(due)

    @staticmethod
    def _risk_families(candidate_type: str):
        if candidate_type == "combined_candidate":
            return ("fall", "hazard")
        if candidate_type == "fall_candidate":
            return ("fall",)
        if candidate_type == "hazard_candidate":
            return ("hazard",)
        return ()
