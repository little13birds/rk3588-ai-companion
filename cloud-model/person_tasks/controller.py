"""High-level controller facade used by tools and deterministic intents."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from typing import Callable, Dict, Optional

from .ros_adapter import RosPersonTaskAdapter


PersonTaskEventHandler = Callable[[Dict[str, object]], None]
SeekStatusGetter = Callable[[], Optional[Dict[str, object]]]
DEFAULT_SEEK_MONITOR_TIMEOUT_SEC = 180.0


class PersonTaskController:
    def __init__(
        self,
        adapter: Optional[RosPersonTaskAdapter] = None,
        *,
        seek_status_getter: Optional[SeekStatusGetter] = None,
        seek_monitor_interval_sec: float = 0.25,
        seek_monitor_start_delay_sec: float = 0.50,
        seek_monitor_timeout_sec: Optional[float] = None,
    ):
        self.adapter = adapter or RosPersonTaskAdapter()
        self._event_handler: Optional[PersonTaskEventHandler] = None
        self._seek_status_getter = seek_status_getter or self._default_seek_status
        self._seek_monitor_interval_sec = max(0.05, float(seek_monitor_interval_sec))
        self._seek_monitor_start_delay_sec = max(0.0, float(seek_monitor_start_delay_sec))
        timeout_sec = _resolve_seek_monitor_timeout_sec(seek_monitor_timeout_sec)
        self._seek_monitor_timeout_sec = max(1.0, timeout_sec)
        self._seek_monitor_lock = threading.Lock()
        self._seek_monitor_stop: Optional[threading.Event] = None
        self._seek_monitor_thread: Optional[threading.Thread] = None

    def set_snapshot_provider(self, provider: Callable[[], Optional[bytes]]) -> None:
        self.adapter.snapshot_provider = provider

    def set_event_handler(self, handler: Optional[PersonTaskEventHandler]) -> None:
        self._event_handler = handler

    def control(self, action: str, target: str) -> Dict[str, object]:
        result = self.adapter.control(action, target)
        normalized_action = str(action or "").strip().lower()
        if normalized_action == "seek" and result.get("ok"):
            self._start_seek_arrival_monitor(result)
        else:
            self._stop_seek_arrival_monitor()
        return result

    def observe_people(self) -> Dict[str, object]:
        return self.adapter.observe_people()

    def ensure_chassis_support_stack(self) -> None:
        self.adapter.ensure_support_stack()

    def shutdown(self) -> None:
        self._stop_seek_arrival_monitor()

    def _start_seek_arrival_monitor(self, result: Dict[str, object]) -> None:
        self._stop_seek_arrival_monitor()
        stop_event = threading.Event()
        target = str(result.get("target") or "nearest")
        target_name = str(result.get("target_name") or target)
        thread = threading.Thread(
            target=self._seek_arrival_monitor_loop,
            args=(stop_event, target, target_name),
            name="person-seek-arrival-monitor",
            daemon=True,
        )
        with self._seek_monitor_lock:
            self._seek_monitor_stop = stop_event
            self._seek_monitor_thread = thread
        thread.start()

    def _stop_seek_arrival_monitor(self) -> None:
        with self._seek_monitor_lock:
            stop_event = self._seek_monitor_stop
            thread = self._seek_monitor_thread
            self._seek_monitor_stop = None
            self._seek_monitor_thread = None
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def _seek_arrival_monitor_loop(
        self,
        stop_event: threading.Event,
        target: str,
        target_name: str,
    ) -> None:
        deadline = time.monotonic() + self._seek_monitor_timeout_sec
        has_seen_active_status = False
        if self._seek_monitor_start_delay_sec > 0:
            stop_event.wait(self._seek_monitor_start_delay_sec)
        while not stop_event.is_set() and time.monotonic() < deadline:
            status = self._safe_seek_status()
            if _seek_status_arrived(status):
                self._emit_event({
                    "event": "seek_arrived",
                    "target": target,
                    "target_name": target_name,
                    "status": status,
                })
                self._stop_active_seek_task()
                break
            if _seek_status_active(status):
                has_seen_active_status = True
            if _seek_status_terminal_without_arrival(status, has_seen_active_status):
                break
            stop_event.wait(self._seek_monitor_interval_sec)
        with self._seek_monitor_lock:
            if self._seek_monitor_stop is stop_event:
                self._seek_monitor_stop = None
                self._seek_monitor_thread = None

    def _safe_seek_status(self) -> Optional[Dict[str, object]]:
        try:
            status = self._seek_status_getter()
        except Exception:
            return None
        return status if isinstance(status, dict) else None

    def _emit_event(self, event: Dict[str, object]) -> None:
        handler = self._event_handler
        if handler is None:
            return
        try:
            handler(event)
        except Exception:
            pass

    def _stop_active_seek_task(self) -> None:
        try:
            self.adapter.stop_person_tasks()
        except Exception:
            pass

    @staticmethod
    def _default_seek_status() -> Optional[Dict[str, object]]:
        url = os.environ.get("PERSON_SEEK_STATUS_URL", "http://127.0.0.1:8092/status.json")
        with urllib.request.urlopen(url, timeout=0.8) as resp:
            return json.loads(resp.read().decode("utf-8"))


def _seek_status_arrived(status: Optional[Dict[str, object]]) -> bool:
    if not status:
        return False
    return status.get("state") == "ARRIVED" or status.get("reason") == "arrived"


def _seek_status_active(status: Optional[Dict[str, object]]) -> bool:
    if not status:
        return False
    state = status.get("state")
    return bool(state and state not in {"IDLE"})


def _seek_status_terminal_without_arrival(
    status: Optional[Dict[str, object]],
    has_seen_active_status: bool = True,
) -> bool:
    if not status:
        return False
    state = status.get("state")
    if state in {"SEARCH_FAILED", "TARGET_LOST"}:
        return True
    return state == "IDLE" and has_seen_active_status


def _resolve_seek_monitor_timeout_sec(explicit_timeout: Optional[float]) -> float:
    if explicit_timeout is not None:
        return float(explicit_timeout)
    raw_value = os.environ.get("PERSON_SEEK_MONITOR_TIMEOUT_SEC")
    if not raw_value:
        return DEFAULT_SEEK_MONITOR_TIMEOUT_SEC
    try:
        return float(raw_value)
    except ValueError:
        return DEFAULT_SEEK_MONITOR_TIMEOUT_SEC
