"""Top-level safety guard service used by main.py."""
from __future__ import annotations

import queue
import threading

import cv2

from .analyzer import SafetyRiskAnalyzer
from .announcer import SafetyAnnouncer
from .config import SafetyGuardConfig
from .monitor import SafetyMonitor
from .recorder import SafetyEventRecorder
from .rknn_runtime import SafetyRknnRuntime
from .ros_camera import RosRgbCamera
from .types import SafetyCandidate


class SafetyGuardService:
    def __init__(self, config: SafetyGuardConfig, speaker=None, cancel_event=None, eye_state=None):
        self.config = config.clamp()
        self.speaker = speaker
        self.cancel_event = cancel_event
        self.eye_state = eye_state
        self.enabled = bool(self.config.enabled)
        self._camera = None
        self._runtime = None
        self._monitor = None
        self._recorder = None
        self._analyzer = None
        self._announcer = None
        self._queue = queue.Queue(maxsize=self.config.analyzer_max_pending)
        self._queue_dropped = 0
        self._stop = threading.Event()
        self._worker = None

    def start(self) -> bool:
        if not self.enabled:
            print("[safety] event=disabled component=service", flush=True)
            return False
        try:
            self._recorder = SafetyEventRecorder(self.config.record_path)
            self._analyzer = SafetyRiskAnalyzer(self.config)
            self._announcer = SafetyAnnouncer(
                self.speaker,
                cancel_event=self.cancel_event,
                min_severity=self.config.announce_min_severity,
                announce_cooldown_sec=self.config.announce_cooldown_sec,
            )
            self._camera = RosRgbCamera(self.config.rgb_topic, self.config.qos_depth)
            self._camera.start()
            self._runtime = SafetyRknnRuntime(self.config)
            self._monitor = SafetyMonitor(self.config, self._runtime, self._camera, self._on_candidate)
            self._worker = threading.Thread(target=self._analysis_loop, name="safety-analyzer", daemon=True)
            self._worker.start()
            self._monitor.start()
            print(
                "[safety] event=started component=service target_hz=%.2f hazard_period=%.2fs lib=%s model_dir=%s"
                % (
                    self.config.target_frequency_hz,
                    self.config.hazard_period_sec,
                    self._runtime.lib_path,
                    self._runtime.model_dir,
                ),
                flush=True,
            )
            return True
        except Exception as exc:
            print(f"[safety] event=start_failed component=service error={exc}", flush=True)
            self.stop()
            if not self.config.fail_open:
                raise
            self.enabled = False
            return False

    def stop(self) -> None:
        self._stop.set()
        if self._monitor:
            self._monitor.stop()
            self._monitor = None
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass
        if self._worker:
            self._worker.join(timeout=3.0)
            self._worker = None
        if self._camera:
            self._camera.stop()
            self._camera = None
        if self._runtime:
            self._runtime.close()
            self._runtime = None

    def set_target_frequency_hz(self, hz: float) -> None:
        self.config.target_frequency_hz = max(0.2, min(float(hz), 20.0))
        if self._monitor:
            self._monitor.set_target_frequency_hz(self.config.target_frequency_hz)

    def get_target_frequency_hz(self) -> float:
        if self._monitor:
            return self._monitor.get_target_frequency_hz()
        return self.config.target_frequency_hz

    def pause(self, reason: str = "") -> None:
        if self._monitor:
            self._monitor.pause(reason)
            print(f"[safety] event=paused component=service reason={reason or 'manual'}", flush=True)

    def resume(self, reason: str = "") -> None:
        if self._monitor:
            self._monitor.resume(reason)
            print(f"[safety] event=resumed component=service reason={reason or 'manual'}", flush=True)


    def camera_snapshot(self):
        if not self._camera:
            return None
        frame, _stamp = self._camera.latest_bgr()
        if frame is None:
            return None
        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return jpg.tobytes() if ok else None

    def status(self) -> dict:
        monitor_status = self._monitor.status() if self._monitor else {
            "active": False,
            "paused": False,
            "target_hz": self.config.target_frequency_hz,
        }
        camera_stats = self._camera.stats() if self._camera else {}
        return {
            "available": True,
            "enabled": self.enabled,
            "running": self._monitor is not None,
            "target_frequency_hz": self.get_target_frequency_hz(),
            "hazard_period_sec": self.config.hazard_period_sec,
            "queue_size": self._queue.qsize(),
            "queue_max": self._queue.maxsize,
            "queue_dropped": self._queue_dropped,
            "monitor": monitor_status,
            "camera": camera_stats,
        }

    def _on_candidate(self, candidate: SafetyCandidate) -> None:
        try:
            self._queue.put_nowait(candidate)
            print(
                f"[safety] event=candidate_queued component=service "
                f"id={candidate.event_id} type={candidate.candidate_type}",
                flush=True,
            )
        except queue.Full:
            self._queue_dropped += 1
            print(
                f"[safety] event=candidate_dropped component=service reason=queue_full "
                f"type={candidate.candidate_type}",
                flush=True,
            )

    def _analysis_loop(self) -> None:
        while not self._stop.is_set():
            item = self._queue.get()
            try:
                if item is None:
                    return
                analysis = self._analyzer.analyze(item)
                phrase_choice = None
                if self._announcer:
                    phrase_choice = self._announcer.prepare_phrase(analysis, item)
                event = self._recorder.record(item, analysis)
                print(
                    "[safety] event=recorded component=service id=%s confirmed=%s severity=%s type=%s"
                    % (
                        event["event_id"],
                        event["confirmed"],
                        event["severity"],
                        event["risk_type"],
                    ),
                    flush=True,
                )
                if analysis.danger and self._announcer:
                    self._announcer.announce(analysis, item, phrase_choice)
            except Exception as exc:
                print(f"[safety] event=analysis_failed component=service error={exc}", flush=True)
            finally:
                self._queue.task_done()
