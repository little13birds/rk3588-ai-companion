"""Shared state and data adapters for the parent dashboard HTTP API."""
from __future__ import annotations

import base64
import json
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import quote, unquote

from .event_store import DashboardEventStore
from .people import PersonIdentityClient

_MIN_DT = datetime.fromtimestamp(0).astimezone()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone()
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone()
    except ValueError:
        pass
    for fmt in ("%Y%m%dT%H%M%S", "%Y-%m-%d %H:%M:%S", "%H:%M"):
        try:
            dt = datetime.strptime(text[: len(fmt)], fmt)
            now = datetime.now()
            if fmt == "%H:%M":
                return now.replace(hour=dt.hour, minute=dt.minute, second=0, microsecond=0)
            return dt.astimezone()
        except ValueError:
            continue
    return None


def _time_hm(value: Any) -> str:
    dt = _parse_dt(value)
    return dt.strftime("%H:%M") if dt else str(value or "")[:5]


def _date_key(value: Any) -> str:
    dt = _parse_dt(value)
    return dt.strftime("%Y-%m-%d") if dt else str(value or "")[:10]


def _date_label(date_key: str) -> str:
    today = datetime.now().date()
    try:
        date = datetime.strptime(date_key, "%Y-%m-%d").date()
    except ValueError:
        return date_key
    if date == today:
        return "今天"
    if date == today - timedelta(days=1):
        return "昨天"
    return date_key


def _safe_text(value: Any, max_len: int = 120) -> str:
    text = str(value or "").strip()
    if len(text) > max_len:
        return text[: max_len - 1] + "..."
    return text


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class SpeechRequest:
    text: str
    source: str = "dashboard"
    created_at: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "text": self.text,
            "source": self.source,
            "created_at": self.created_at or _now_iso(),
        }


class DashboardState:
    """Thread-safe state used by the main loop and the dashboard HTTP thread."""

    def __init__(
        self,
        safety_record_dir: str = "~/cloud-model/safety_records",
        reading_capture_dir: str = "reading_captures",
        event_db_path: Optional[str] = None,
        safety_active_sec: int = 90,
        alert_window_sec: int = 900,
    ):
        self.safety_record_dir = Path(safety_record_dir).expanduser()
        self.reading_capture_dir = Path(reading_capture_dir).expanduser()
        self.event_db_path = Path(
            event_db_path
            or os.environ.get("DASHBOARD_EVENT_DB", "dashboard_records/dashboard.db")
        ).expanduser()
        self.safety_active_sec = max(10, int(safety_active_sec))
        self.alert_window_sec = max(30, int(alert_window_sec))
        self._event_store = DashboardEventStore(str(self.event_db_path))

        self._lock = threading.RLock()
        self._speech_queue: queue.Queue[Dict[str, str]] = queue.Queue(maxsize=20)
        self._conversation: List[Dict[str, str]] = []
        self._activities: List[Dict[str, str]] = []
        self._reading_pages_by_date: Dict[str, int] = {}
        self._reading_minutes_by_date: Dict[str, int] = {}
        self._last_mode_change = _now_iso()
        self._mode = "normal"
        self._is_processing = False
        self._is_awake = False
        self._last_speech_source = ""
        self._scheduler_status_provider: Optional[Callable[[], Dict[str, Any]]] = None
        self._camera_snapshot_provider: Optional[Callable[[], Optional[bytes]]] = None
        self._reading_camera_snapshot_provider: Optional[Callable[[], Optional[bytes]]] = None
        self._move_handler: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None
        self._move_status_provider: Optional[Callable[[], Dict[str, Any]]] = None
        self._people_client: Any = PersonIdentityClient()
        self._person_task_controller: Any = None
        self._people_candidates: Dict[str, Dict[str, Any]] = {}
        self._people_candidate_ttl_sec = 300.0
        self._person_task_status: Dict[str, Any] = {
            "active": False,
            "action": "",
            "target": "",
            "started_at": 0.0,
            "timeout_sec": 0,
            "last_result": {},
        }
        self._person_task_timer: Optional[threading.Timer] = None

        self._env_cache: Optional[Dict[str, Any]] = None
        self._env_cache_t = 0.0
        self._env_refreshing = False

        self._sleep = self._event_store.get_setting("sleep", {
            "bedtime": "21:00",
            "aid_type": "whitenoise",
            "aid_duration_min": 20,
            "aid_duration_sec": 1200,
            "aid_active": False,
            "aid_ends_at": 0.0,
            "auto_aid": True,
            "remind_text": "宝贝，该准备睡觉啦",
            "children": [],
            "grace_minutes": 10,
            "remind_interval_min": 5,
        })
        self._sleep.setdefault("children", [])
        self._sleep.setdefault("grace_minutes", 10)
        self._sleep.setdefault("remind_interval_min", 5)
        self._ui_config = self._event_store.get_setting("dashboard_ui", {
            "cameraRefreshMs": 1000,
            "statusRefreshMs": 2000,
            "systemRefreshMs": 5000,
            "historyPageSize": 6,
            "theme": "day",
        })
        self._sleep_presence: Dict[str, float] = {}
        self._sleep_presence_ttl_sec = 30.0

    @classmethod
    def from_env(cls) -> "DashboardState":
        return cls(
            safety_record_dir=os.environ.get(
                "DASHBOARD_SAFETY_RECORD_DIR",
                os.environ.get("SAFETY_GUARD_RECORD_DIR", "~/cloud-model/safety_records"),
            ),
            reading_capture_dir=os.environ.get("READING_CAPTURE_DIR", "reading_captures"),
            event_db_path=os.environ.get("DASHBOARD_EVENT_DB", "dashboard_records/dashboard.db"),
            safety_active_sec=_int_env("DASHBOARD_SAFETY_ACTIVE_SEC", 90),
            alert_window_sec=_int_env("DASHBOARD_ALERT_WINDOW_SEC", 900),
        )

    def set_runtime(
        self,
        mode: Optional[str] = None,
        is_processing: Optional[bool] = None,
        is_awake: Optional[bool] = None,
    ) -> None:
        with self._lock:
            if mode is not None and mode != self._mode:
                self._mode = mode
                self._last_mode_change = _now_iso()
                self.add_activity(
                    "system",
                    f"模式切换为 {mode}",
                    locked=True,
                    kind="system",
                    actor="system",
                    title="模式切换",
                    meta={"mode": mode},
                )
            if is_processing is not None:
                self._is_processing = bool(is_processing)
            if is_awake is not None:
                self._is_awake = bool(is_awake)

    def set_scheduler_status_provider(self, provider: Callable[[], Dict[str, Any]]) -> None:
        self._scheduler_status_provider = provider


    def set_camera_snapshot_provider(self, provider: Callable[[], Optional[bytes]]) -> None:
        self._camera_snapshot_provider = provider

    def set_reading_camera_snapshot_provider(self, provider: Callable[[], Optional[bytes]]) -> None:
        self._reading_camera_snapshot_provider = provider

    def set_move_handler(
        self,
        handler: Callable[[str, Dict[str, Any]], Dict[str, Any]],
        status_provider: Optional[Callable[[], Dict[str, Any]]] = None,
    ) -> None:
        self._move_handler = handler
        self._move_status_provider = status_provider

    def set_people_client(self, client: Any) -> None:
        self._people_client = client

    def set_person_task_controller(self, controller: Any) -> None:
        self._person_task_controller = controller

    def _scheduler_snapshot(self) -> Dict[str, Any]:
        provider = self._scheduler_status_provider
        if not provider:
            return {
                "enabled": False,
                "mode": self._mode,
                "leases": [],
                "resources": {},
                "conflicts": [],
                "error": "scheduler_unavailable",
            }
        try:
            data = provider()
            return data if isinstance(data, dict) else {"enabled": False, "error": "invalid_scheduler_snapshot"}
        except Exception as exc:
            return {
                "enabled": False,
                "mode": self._mode,
                "leases": [],
                "resources": {},
                "conflicts": [],
                "error": f"scheduler_error:{type(exc).__name__}",
            }

    def system_mode(self) -> Dict[str, Any]:
        data = self._scheduler_snapshot()
        return {
            "enabled": bool(data.get("enabled")),
            "mode": data.get("mode", self._mode),
            "reading": data.get("reading", {}),
            "safety_guard": data.get("safety_guard", {}),
            "config": data.get("config", {}),
            "error": data.get("error", ""),
        }

    def system_resources(self) -> Dict[str, Any]:
        data = self._scheduler_snapshot()
        return {
            "enabled": bool(data.get("enabled")),
            "mode": data.get("mode", self._mode),
            "leases": list(data.get("leases") or []),
            "resources": dict(data.get("resources") or {}),
        }

    def system_conflicts(self) -> Dict[str, Any]:
        data = self._scheduler_snapshot()
        return {
            "enabled": bool(data.get("enabled")),
            "mode": data.get("mode", self._mode),
            "conflicts": list(data.get("conflicts") or []),
            "history": list(data.get("history") or []),
        }

    def system_features(self) -> Dict[str, Any]:
        with self._lock:
            runtime_mode = self._mode
            person_task_active = bool(self._person_task_status.get("active"))
            person_task_action = str(self._person_task_status.get("action") or "")
            person_task_target = str(self._person_task_status.get("target") or "")
        movement = {
            "enabled": False,
            "reserved": True,
            "status": "reserved",
            "detail": "dashboard move API is available; chassis adapter is not connected",
        }
        if self._move_status_provider:
            try:
                movement.update(self._move_status_provider())
            except Exception as exc:
                movement.update({
                    "enabled": False,
                    "reserved": True,
                    "status": "error",
                    "detail": type(exc).__name__,
                })
        elif self._move_handler:
            movement.update({
                "enabled": True,
                "reserved": False,
                "status": "connected",
                "detail": "dashboard move handler is connected",
            })
        manual_allowed = runtime_mode == "normal" and not person_task_active
        movement["manual_allowed"] = manual_allowed
        if movement.get("enabled") and not movement.get("reserved") and not manual_allowed:
            movement["status"] = "busy"
            if runtime_mode != "normal":
                movement["detail"] = f"当前 {runtime_mode} 模式，方向键已禁用，急停仍可用"
            else:
                movement["detail"] = f"人物任务运行中({person_task_action}:{person_task_target})，方向键已禁用，急停仍可用"
        find_child = {
            "enabled": bool(self._person_task_controller),
            "reserved": not bool(self._person_task_controller),
            "detail": "person seek module is connected" if self._person_task_controller else "person seek module is not connected to dashboard yet",
        }
        return {
            "movement": movement,
            "find_child": find_child,
            "camera_history": {
                "enabled": True,
                "reserved": False,
                "detail": "safety and reading snapshots are exposed by date",
            },
            "sleep_rules": {
                "enabled": True,
                "reserved": False,
                "detail": "bedtime rules use configured child presence when available",
            },
            "abnormal_sound": {
                "enabled": False,
                "reserved": True,
                "detail": "sound event model is not connected",
            },
            "realtime_socket": {
                "enabled": False,
                "reserved": True,
                "detail": "frontend placeholder exists; HTTP polling remains active",
            },
        }

    def child_status(self) -> Dict[str, str]:
        with self._lock:
            mode = self._mode
            processing = self._is_processing
            awake = self._is_awake
            since = self._last_mode_change
        if mode == "reading":
            return {"status": "reading", "since": since, "detail": "读书模式"}
        if mode == "story":
            return {"status": "playing", "since": since, "detail": "讲故事中"}
        if processing:
            return {"status": "playing", "since": since, "detail": "正在对话"}
        if awake:
            return {"status": "quiet", "since": since, "detail": "正在监听"}
        return {"status": "quiet", "since": since, "detail": "待机中"}

    def _append_event(
        self,
        *,
        kind: str,
        level: str,
        actor: str,
        title: str,
        text: str,
        ts: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            return self._event_store.append(
                kind=kind,
                level=level,
                actor=actor,
                title=title,
                text=text,
                ts=ts,
                meta=meta,
            )
        except Exception as exc:
            print(
                f"[dashboard] event=event_append_failed error_type={type(exc).__name__} error={exc}",
                flush=True,
            )
            ts = ts or _now_iso()
            return {
                "id": "",
                "ts": ts,
                "date_key": _date_key(ts),
                "kind": kind,
                "level": level,
                "actor": actor,
                "title": title,
                "text": text,
                "meta": dict(meta or {}),
            }

    @staticmethod
    def _conversation_title(msg_type: str, source: str = "") -> str:
        if msg_type == "child":
            return "孩子"
        if msg_type == "parent":
            return "家长播报" if source else "家长"
        if source == "sleep_remind":
            return "入睡提醒"
        if source == "parent":
            return "家长播报"
        return "小智"

    def add_conversation(
        self,
        msg_type: str,
        text: str,
        when: Optional[str] = None,
        source: str = "",
    ) -> None:
        text = _safe_text(text, 500)
        if not text:
            return
        when = when or _now_iso()
        source = str(source or "")
        event = self._append_event(
            kind="conversation",
            level="info",
            actor=msg_type,
            title=self._conversation_title(msg_type, source),
            text=text,
            ts=when,
            meta={"type": msg_type, "source": source},
        )
        item = {
            "id": event.get("id", ""),
            "type": msg_type,
            "time": when,
            "text": text,
            "source": source,
        }
        with self._lock:
            self._conversation.append(item)
            self._conversation = self._conversation[-80:]

    def conversation_summary(self) -> List[Dict[str, str]]:
        events = self._event_store.list_events(kinds=["conversation"], limit=30)
        if events:
            return [self._conversation_from_event(event) for event in events]
        with self._lock:
            return list(self._conversation[-30:])

    @staticmethod
    def _conversation_from_event(event: Dict[str, Any]) -> Dict[str, str]:
        meta = event.get("meta") or {}
        msg_type = str(meta.get("type") or event.get("actor") or "robot")
        return {
            "id": str(event.get("id") or ""),
            "type": msg_type,
            "time": str(event.get("ts") or ""),
            "text": str(event.get("text") or ""),
            "source": str(meta.get("source") or ""),
        }

    def add_activity(
        self,
        activity_type: str,
        text: str,
        when: Optional[str] = None,
        locked: bool = False,
        kind: Optional[str] = None,
        actor: str = "system",
        title: str = "",
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        when = when or _now_iso()
        clean_text = _safe_text(text, 180)
        item = {"type": activity_type, "time": when, "text": clean_text}
        level = "warn" if activity_type in {"warn", "danger"} else "info"
        event_kind = kind or ("system" if activity_type == "system" else "activity")
        event_title = title or self._activity_title(activity_type, event_kind)
        event_meta = {"type": activity_type}
        event_meta.update(meta or {})
        self._append_event(
            kind=event_kind,
            level=level,
            actor=actor,
            title=event_title,
            text=clean_text,
            ts=when,
            meta=event_meta,
        )
        if locked:
            self._activities.append(item)
            self._activities = self._activities[-120:]
            return
        with self._lock:
            self._activities.append(item)
            self._activities = self._activities[-120:]

    @staticmethod
    def _activity_title(activity_type: str, kind: str) -> str:
        if kind == "system":
            return "系统状态"
        if kind == "reading":
            return "读书记录"
        if kind == "sleep":
            return "睡眠提醒"
        if kind == "parent_action":
            return "家长操作"
        if activity_type in {"warn", "danger"}:
            return "安全提醒"
        return "今日活动"

    def queue_speech(self, text: str, source: str = "dashboard") -> bool:
        text = _safe_text(text, 180)
        if not text:
            return False
        req = SpeechRequest(text=text, source=source, created_at=_now_iso()).to_dict()
        try:
            self._speech_queue.put_nowait(req)
        except queue.Full:
            return False
        with self._lock:
            self._last_speech_source = source
        if source == "parent":
            self.add_conversation("parent", text, when=req["created_at"], source=source)
            self.add_activity(
                "info",
                "家长发送语音消息",
                when=req["created_at"],
                kind="parent_action",
                actor="parent",
                title="家长播报",
                meta={"source": source},
            )
        elif source == "sleep_remind":
            self.add_conversation("parent", text, when=req["created_at"], source=source)
            self.add_activity(
                "info",
                "家长发送入睡提醒",
                when=req["created_at"],
                kind="sleep",
                actor="parent",
                title="入睡提醒",
                meta={"source": source},
            )
        return True

    def pop_speech_request(self) -> Optional[Dict[str, str]]:
        try:
            return self._speech_queue.get_nowait()
        except queue.Empty:
            return None

    def complete_speech_request(self) -> None:
        try:
            self._speech_queue.task_done()
        except ValueError:
            pass

    def record_reading_result(self, response_text: str, successful: bool) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            if successful:
                self._reading_pages_by_date[today] = self._reading_pages_by_date.get(today, 0) + 1
                self._reading_minutes_by_date[today] = self._reading_minutes_by_date.get(today, 0) + 1
                self.add_activity(
                    "info",
                    "完成一页读书朗读",
                    locked=True,
                    kind="reading",
                    title="读书完成",
                )
            elif response_text:
                self.add_activity(
                    "info",
                    "读书拍摄未能确认文字",
                    locked=True,
                    kind="reading",
                    title="读书未完成",
                )

    def reading_report(self) -> Dict[str, int]:
        today = datetime.now().strftime("%Y-%m-%d")
        pages = self._reading_capture_count(today)
        with self._lock:
            pages = max(pages, self._reading_pages_by_date.get(today, 0))
            minutes = self._reading_minutes_by_date.get(today, pages)
        return {
            "books_read": 1 if pages else 0,
            "pages_total": pages,
            "minutes": minutes,
        }

    def reading_records(self) -> Dict[str, Dict[str, int]]:
        records: Dict[str, Dict[str, int]] = {}
        for date_key, count in self._reading_capture_counts().items():
            if count:
                records[_date_label(date_key)] = {"未匹配书名": count}
        with self._lock:
            for date_key, count in self._reading_pages_by_date.items():
                if count:
                    label = _date_label(date_key)
                    records.setdefault(label, {})
                    records[label]["未匹配书名"] = max(records[label].get("未匹配书名", 0), count)
        return records

    def environment(self, ttl_sec: float = 5.0) -> Dict[str, Any]:
        now = time.monotonic()
        refresh_sync = False
        with self._lock:
            if self._env_cache and now - self._env_cache_t < ttl_sec:
                return dict(self._env_cache)
            if self._env_cache is None and not self._env_refreshing:
                self._env_refreshing = True
                refresh_sync = True
            elif not self._env_refreshing:
                self._env_refreshing = True
                threading.Thread(target=self._refresh_environment, name="dashboard-env", daemon=True).start()
            data = dict(self._env_cache or {
                "temperature": 0.0,
                "humidity": 0.0,
                "light": 0.0,
                "temp_warn": False,
                "light_warn": False,
                "errors": ["not_ready"],
            })
            if not refresh_sync:
                return data

        data = self._refresh_environment()
        if data.get("errors"):
            with self._lock:
                if self._env_cache and self._env_cache.get("errors") != ["not_ready"]:
                    return dict(self._env_cache)
            return data
        return data

    def _refresh_environment(self) -> Dict[str, Any]:
        now = time.monotonic()

        temperature = None
        humidity = None
        light = None
        errors: List[str] = []
        try:
            from sensors.sensors import read_temperature

            temp_data = read_temperature()
            temperature = float(temp_data.get("temperature"))
            humidity = float(temp_data.get("humidity"))
        except Exception as exc:
            errors.append(f"temperature:{type(exc).__name__}")

        try:
            from sensors.sensors import read_light

            light = float(read_light())
        except Exception as exc:
            errors.append(f"light:{type(exc).__name__}")

        with self._lock:
            previous = self._env_cache or {}
            data = {
                "temperature": temperature if temperature is not None else previous.get("temperature", 0.0),
                "humidity": humidity if humidity is not None else previous.get("humidity", 0.0),
                "light": light if light is not None else previous.get("light", 0.0),
                "temp_warn": bool(temperature is not None and (temperature < 18.0 or temperature > 30.0)),
                "light_warn": bool(light is not None and light < 300.0),
                "errors": errors,
            }
            self._env_cache = data
            self._env_cache_t = now
            self._env_refreshing = False
            return dict(data)

    def camera_snapshot_with_source(self) -> tuple[Optional[bytes], str]:
        mode = self._mode
        if mode == "reading" and self._reading_camera_snapshot_provider:
            try:
                jpg = self._reading_camera_snapshot_provider()
            except Exception:
                jpg = None
            if jpg:
                return jpg, "reading_arm"

        provider = self._camera_snapshot_provider
        if not provider:
            return None, "reading_arm" if mode == "reading" else "platform_camera"
        try:
            return provider(), "platform_camera"
        except Exception:
            return None, "platform_camera"

    def camera_snapshot(self) -> Optional[bytes]:
        jpg, _source = self.camera_snapshot_with_source()
        return jpg

    def camera_source(self) -> Dict[str, Any]:
        mode = self._mode
        source = "reading_arm" if mode == "reading" else "platform_camera"
        return {
            "mode": mode,
            "source": source,
            "label": "机械臂读书摄像头" if source == "reading_arm" else "平台摄像头",
        }

    def v3_config(self) -> Dict[str, Any]:
        sleep = self.sleep_status()
        with self._lock:
            ui = dict(self._ui_config)
        return {
            "ok": True,
            "config": {
                "cameraRefreshMs": int(ui.get("cameraRefreshMs") or 1000),
                "statusRefreshMs": int(ui.get("statusRefreshMs") or 2000),
                "systemRefreshMs": int(ui.get("systemRefreshMs") or 5000),
                "historyPageSize": int(ui.get("historyPageSize") or 6),
                "sleepReminderText": str(sleep.get("remind_text") or "宝贝，该准备睡觉啦。"),
                "sleepTime": str(sleep.get("bedtime") or "21:00")[:5],
                "sleepChildren": list(sleep.get("children") or []),
                "theme": "night" if ui.get("theme") == "night" else "day",
            },
        }

    def update_v3_config(self, body: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            ui = dict(self._ui_config)
            ui["cameraRefreshMs"] = max(250, min(int(body.get("cameraRefreshMs") or ui.get("cameraRefreshMs") or 1000), 10000))
            ui["statusRefreshMs"] = max(500, min(int(body.get("statusRefreshMs") or ui.get("statusRefreshMs") or 2000), 30000))
            ui["systemRefreshMs"] = max(1000, min(int(body.get("systemRefreshMs") or ui.get("systemRefreshMs") or 5000), 60000))
            ui["historyPageSize"] = max(3, min(int(body.get("historyPageSize") or ui.get("historyPageSize") or 6), 24))
            theme = str(body.get("theme") or ui.get("theme") or "day").strip().lower()
            ui["theme"] = "night" if theme == "night" else "day"
            self._ui_config = ui
            self._event_store.set_setting("dashboard_ui", ui)

        sleep_body: Dict[str, Any] = {}
        if body.get("sleepTime") is not None:
            sleep_body["bedtime"] = str(body.get("sleepTime") or "21:00")[:5]
        if body.get("sleepReminderText") is not None:
            text = _safe_text(body.get("sleepReminderText") or "宝贝，该准备睡觉啦。", 80)
            with self._lock:
                self._sleep["remind_text"] = text
        if body.get("sleepChildren") is not None:
            sleep_body["children"] = body.get("sleepChildren")
        if sleep_body:
            self.update_sleep_settings(sleep_body)
        elif body.get("sleepReminderText") is not None:
            with self._lock:
                self._persist_sleep_locked()
        return self.v3_config()

    def v3_status(self) -> Dict[str, Any]:
        source = self.camera_source()
        env = self.environment()
        child = self.child_status()
        sleep = self.sleep_status()
        safety = self.safety_status()
        active_warnings = [name for name, item in safety.items() if not item.get("ok", True)]
        visible_children = list(sleep.get("visible_children") or [])
        return {
            "ok": True,
            "timestamp": _now_iso(),
            "camera": {
                "source": source.get("source", ""),
                "label": source.get("label", "平台摄像头"),
                "fpsTarget": 15,
                "frameUrl": f"/api/camera/snapshot?t={int(time.time())}",
            },
            "environment": {
                "temperatureC": env.get("temperature", 0.0),
                "humidity": env.get("humidity", 0.0),
            },
            "child": {
                "state": child.get("detail") or child.get("status") or "--",
                "visibleChildren": visible_children,
            },
            "safety": {
                "state": "提醒" if active_warnings else "安全",
                "summary": "、".join(active_warnings) if active_warnings else "无确认危险事件",
            },
            "sleep": {
                "time": sleep.get("bedtime", "--"),
                "children": list(sleep.get("children") or []),
                "aid": {
                    "active": bool(sleep.get("aid_active")),
                    "mode": sleep.get("aid_type", "whitenoise"),
                    "remainingSec": int(sleep.get("aid_remaining_sec") or 0),
                },
                "reminderText": sleep.get("remind_text", "宝贝，该准备睡觉啦。"),
            },
        }

    def v3_system_components(self) -> Dict[str, Any]:
        mode = self.system_mode()
        resources = self.system_resources()
        conflicts = self.system_conflicts()
        features = self.system_features()
        sleep = self.sleep_status()
        now = _now_iso()
        movement = features.get("movement") or {}
        return {
            "ok": True,
            "refreshMs": int(self._ui_config.get("systemRefreshMs") or 5000),
            "updatedAt": now,
            "components": [
                {"name": "ASR/KWS", "status": "running", "detail": f"mode={self._mode}", "resource": "audio", "updatedAt": now},
                {"name": "TTS", "status": "running", "detail": "主程序播报队列", "resource": "audio", "updatedAt": now},
                {"name": "平台相机", "status": "running" if self.camera_source().get("source") == "platform_camera" else "standby", "detail": self.camera_source().get("label", ""), "resource": "camera", "updatedAt": now},
                {"name": "安全守护", "status": "running", "detail": self.v3_status()["safety"]["summary"], "resource": "npu", "updatedAt": now},
                {"name": "底盘控制", "status": "running" if movement.get("enabled") else "standby", "detail": movement.get("detail", ""), "resource": "chassis", "updatedAt": now},
                {"name": "资源调度器", "status": "running" if mode.get("enabled") else "standby", "detail": f"leases={len(resources.get('leases') or [])}, conflicts={len(conflicts.get('conflicts') or [])}", "resource": "scheduler", "updatedAt": now},
                {"name": "睡眠监测", "status": "running", "detail": f"bedtime={sleep.get('bedtime', '--')}", "resource": "dashboard", "updatedAt": now},
            ],
        }

    def v3_history(self, date_key: str) -> Dict[str, Any]:
        selected = self._v3_clamp_date(date_key)
        items = self._v3_history_items_for_date(selected, categories=None)
        categories = []
        for category_id, title in self._v3_history_types():
            category_items = [item for item in items if item["category"] == category_id]
            categories.append({"id": category_id, "title": title, "items": category_items})
        return {
            "ok": True,
            "date": selected,
            "categories": categories,
            "dateRows": [{"date": selected, "items": items}],
        }

    def v3_history_gallery(
        self,
        *,
        category: str = "",
        date_from: str = "",
        date_to: str = "",
    ) -> Dict[str, Any]:
        selected_to = self._v3_clamp_date(date_to)
        selected_from = self._v3_clamp_date(date_from or selected_to)
        start = datetime.strptime(selected_from, "%Y-%m-%d").date()
        end = datetime.strptime(selected_to, "%Y-%m-%d").date()
        if start > end:
            start, end = end, start
        if (end - start).days > 30:
            start = end - timedelta(days=30)
        categories = self._v3_selected_categories(category)
        rows = []
        current = end
        while current >= start:
            date_key = current.isoformat()
            rows.append({
                "date": date_key,
                "items": self._v3_history_items_for_date(date_key, categories=categories),
            })
            current -= timedelta(days=1)
        return {
            "ok": True,
            "dateFrom": start.isoformat(),
            "dateTo": end.isoformat(),
            "category": ",".join(categories) if categories else "all",
            "selectedCategories": categories,
            "types": [{"id": category_id, "title": title} for category_id, title in self._v3_history_types()],
            "dateRows": rows,
        }

    @staticmethod
    def _v3_history_types() -> List[tuple[str, str]]:
        return [
            ("safety", "危险事件"),
            ("sleep", "睡眠抓拍"),
            ("reading", "读书记录"),
            ("system", "系统切换"),
        ]

    def _v3_selected_categories(self, raw: str) -> List[str]:
        requested = {part.strip() for part in str(raw or "").split(",") if part.strip()}
        return [category_id for category_id, _title in self._v3_history_types() if category_id in requested]

    @staticmethod
    def _v3_clamp_date(value: Any) -> str:
        today = datetime.now().date()
        lower = today - timedelta(days=30)
        try:
            parsed = datetime.strptime(str(value or today.isoformat())[:10], "%Y-%m-%d").date()
        except ValueError:
            parsed = today
        if parsed > today:
            parsed = today
        if parsed < lower:
            parsed = lower
        return parsed.isoformat()

    def _v3_history_items_for_date(
        self,
        date_key: str,
        *,
        categories: Optional[List[str]],
    ) -> List[Dict[str, Any]]:
        snapshots = self.camera_history(date_key).get("snapshots") or []
        items = []
        category_filter = set(categories or [])
        for index, snapshot in enumerate(snapshots):
            path = str(snapshot.get("path") or "")
            category = "reading" if path.startswith("reading/") else "safety"
            if category_filter and category not in category_filter:
                continue
            reason = str(snapshot.get("reason") or "历史画面")
            item_id = f"{category}-{date_key}-{index}"
            items.append({
                "id": item_id,
                "date": date_key,
                "category": category,
                "categoryTitle": "读书记录" if category == "reading" else "危险事件",
                "time": str(snapshot.get("time") or ""),
                "event": reason,
                "source": "机械臂相机" if category == "reading" else "平台摄像头",
                "imageUrl": f"/api/camera/history/image/{quote(path)}",
            })
        items.sort(key=lambda item: (item.get("time", ""), item.get("category", "")), reverse=True)
        return items

    def people_registry(self) -> Dict[str, Any]:
        try:
            self._ensure_people_service()
            data = self._people_client.list_people()
            if isinstance(data, dict):
                data.setdefault("ok", True)
                return data
        except Exception as exc:
            return {"ok": False, "error": type(exc).__name__, "people": []}
        return {"ok": False, "error": "invalid_people_result", "people": []}

    def delete_person(self, body: Dict[str, Any]) -> Dict[str, Any]:
        unique_name = str(body.get("unique_name") or body.get("person_id") or body.get("name") or "").strip()
        if not unique_name:
            return {"ok": False, "error": "missing_unique_name"}
        try:
            self._ensure_people_service()
            result = self._people_client.delete_person(unique_name)
        except Exception as exc:
            return {"ok": False, "error": type(exc).__name__}
        self.add_activity(
            "info",
            f"家长删除人物: {unique_name}",
            kind="parent_action",
            actor="parent",
            title="人物管理",
            meta={"unique_name": unique_name},
        )
        return result if isinstance(result, dict) else {"ok": False, "error": "invalid_delete_result"}

    def people_candidates_from_upload(self, body: Dict[str, Any]) -> Dict[str, Any]:
        image_b64 = str(body.get("image_b64") or body.get("jpeg_b64") or body.get("image") or "")
        if "," in image_b64 and image_b64.split(",", 1)[0].startswith("data:"):
            image_b64 = image_b64.split(",", 1)[1]
        try:
            jpeg_bytes = base64.b64decode(image_b64, validate=False)
        except Exception:
            return {"ok": False, "error": "invalid_image_b64", "known_faces": [], "candidates": []}
        return self.people_candidates_from_image(jpeg_bytes, source="upload")

    def people_candidates_from_camera(self) -> Dict[str, Any]:
        jpeg_bytes = self.camera_snapshot()
        if not jpeg_bytes:
            return {"ok": False, "error": "snapshot_unavailable", "known_faces": [], "candidates": []}
        return self.people_candidates_from_image(jpeg_bytes, source="platform_camera")

    def refresh_sleep_presence_from_identity(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = now or datetime.now()
        with self._lock:
            children = list(self._sleep.get("children") or [])
            mode = self._mode
        if not children:
            return {"ok": True, "skipped": "no_children", "visible_children": []}
        if mode != "normal":
            return {"ok": True, "skipped": f"mode:{mode}", "visible_children": []}

        jpeg_bytes, source = self.camera_snapshot_with_source()
        if source != "platform_camera":
            return {"ok": True, "skipped": f"camera:{source}", "visible_children": []}
        if not jpeg_bytes:
            return {"ok": False, "error": "snapshot_unavailable", "visible_children": []}

        try:
            self._ensure_people_service()
            result = self._people_client.capture_candidates_from_jpeg(
                jpeg_bytes,
                source="sleep_presence",
            )
        except Exception as exc:
            return {"ok": False, "error": type(exc).__name__, "visible_children": []}

        known_faces = result.get("known_faces") if isinstance(result, dict) else []
        child_set = set(children)
        visible = []
        for face in known_faces or []:
            name = str(face.get("unique_name") or face.get("name") or "").strip()
            if name and name in child_set and name not in visible:
                visible.append(name)
        for name in visible:
            self.update_sleep_presence({"unique_name": name, "visible": True}, now=now)
        return {
            "ok": True,
            "visible_children": visible,
            "known_count": len(known_faces or []),
        }

    def people_candidates_from_image(self, jpeg_bytes: bytes, source: str = "upload") -> Dict[str, Any]:
        if not jpeg_bytes:
            return {"ok": False, "error": "image_empty", "known_faces": [], "candidates": []}
        try:
            self._ensure_people_service()
            result = self._people_client.capture_candidates_from_jpeg(jpeg_bytes, source=source)
        except Exception as exc:
            return {"ok": False, "error": type(exc).__name__, "known_faces": [], "candidates": []}
        candidates = []
        now = time.time()
        self._prune_people_candidates(now)
        for candidate in result.get("candidates") or []:
            if not isinstance(candidate, dict) or not candidate.get("embedding"):
                continue
            candidate_id = uuid.uuid4().hex
            cached = dict(candidate)
            cached["created_at"] = now
            cached["candidate_id"] = candidate_id
            with self._lock:
                self._people_candidates[candidate_id] = cached
            public = {
                key: value
                for key, value in candidate.items()
                if key not in {"embedding"}
            }
            public["candidate_id"] = candidate_id
            candidates.append(public)
        return {
            "ok": bool(result.get("ok", True)),
            "known_faces": list(result.get("known_faces") or []),
            "candidates": candidates,
            "num_people": int(result.get("num_people") or 0),
            "error": str(result.get("error") or ""),
        }

    def enroll_person(self, body: Dict[str, Any]) -> Dict[str, Any]:
        candidate_id = str(body.get("candidate_id") or "").strip()
        unique_name = str(body.get("unique_name") or body.get("name") or "").strip()
        if not candidate_id:
            return {"ok": False, "error": "missing_candidate_id"}
        if not unique_name:
            return {"ok": False, "error": "missing_unique_name"}
        now = time.time()
        self._prune_people_candidates(now)
        with self._lock:
            candidate = self._people_candidates.get(candidate_id)
        if not candidate:
            return {"ok": False, "error": "candidate_not_found"}
        payload = {
            "unique_name": unique_name,
            "embedding": candidate.get("embedding"),
            "quality": candidate.get("quality", 0.0),
            "crop_jpeg_b64": candidate.get("crop_jpeg_b64", ""),
        }
        try:
            self._ensure_people_service()
            result = self._people_client.enroll_face(payload)
        except Exception as exc:
            return {"ok": False, "error": type(exc).__name__}
        if result.get("ok", False):
            with self._lock:
                self._people_candidates.pop(candidate_id, None)
            self.add_activity(
                "info",
                f"家长录入人物: {unique_name}",
                kind="parent_action",
                actor="parent",
                title="人物管理",
                meta={"unique_name": unique_name},
            )
        return result if isinstance(result, dict) else {"ok": False, "error": "invalid_enroll_result"}

    def _prune_people_candidates(self, now: Optional[float] = None) -> None:
        now = now or time.time()
        cutoff = now - self._people_candidate_ttl_sec
        with self._lock:
            stale = [cid for cid, item in self._people_candidates.items() if float(item.get("created_at") or 0.0) < cutoff]
            for cid in stale:
                self._people_candidates.pop(cid, None)

    def _ensure_people_service(self) -> None:
        controller = self._person_task_controller
        adapter = getattr(controller, "adapter", None)
        ensure = getattr(adapter, "ensure_tracker_server", None)
        if callable(ensure):
            ensure()

    def safety_status(self) -> Dict[str, Dict[str, Any]]:
        events = self._safety_events()
        cutoff = datetime.now().astimezone() - timedelta(seconds=self.safety_active_sec)
        status = {
            "fall": {"ok": True, "detail": ""},
            "abnormal_sound": {"ok": True, "detail": ""},
            "dangerous_item": {"ok": True, "detail": ""},
            "in_sight": {"ok": True, "detail": ""},
        }
        for event in events:
            if not event.get("confirmed"):
                continue
            dt = _parse_dt(event.get("created_at"))
            if dt and dt < cutoff:
                continue
            family = self._event_family(event)
            if family == "fall" and status["fall"]["ok"]:
                status["fall"] = self._warn_status(event)
            elif family == "dangerous_item" and status["dangerous_item"]["ok"]:
                status["dangerous_item"] = self._warn_status(event)
        return status

    def alerts(self) -> List[Dict[str, str]]:
        cutoff = datetime.now().astimezone() - timedelta(seconds=self.alert_window_sec)
        alerts = []
        for event in self._safety_events(limit=20):
            if not event.get("confirmed"):
                continue
            dt = _parse_dt(event.get("created_at"))
            if dt and dt < cutoff:
                continue
            family = self._event_family(event)
            if family == "fall":
                text = "检测到可能跌倒，请及时查看"
            elif family == "dangerous_item":
                text = "发现需关注物品，请及时检查"
            else:
                text = "发现安全风险，请及时查看"
            alerts.append({
                "type": "warn",
                "text": text,
                "time": event.get("created_at", ""),
                "event_id": event.get("event_id", ""),
            })
        return alerts[:5]

    def dashboard_events(
        self,
        date_key: Optional[str] = None,
        limit: int = 100,
        kinds: Optional[Iterable[str]] = None,
    ) -> List[Dict[str, Any]]:
        return self._event_store.list_events(
            date_key=date_key,
            kinds=kinds,
            limit=limit,
        )

    def dashboard_timeline(
        self,
        date_key: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        date_key = date_key or datetime.now().strftime("%Y-%m-%d")
        limit = max(1, min(int(limit or 100), 500))
        events = self.dashboard_events(date_key=date_key, limit=limit)
        events.extend(self._safety_timeline_events(date_key, limit=50))
        events.extend(self._reading_timeline_events(date_key))
        events.sort(key=lambda item: (
            _parse_dt(item.get("ts")) or _MIN_DT,
            int(item.get("seq") or 0),
            item.get("id", ""),
        ))
        return events[-limit:]

    def activity(self) -> List[Dict[str, str]]:
        today = datetime.now().strftime("%Y-%m-%d")
        items = [self._legacy_activity_from_event(event)
                 for event in self.dashboard_timeline(today, limit=80)]
        return items[-60:]

    def _legacy_activity_from_event(self, event: Dict[str, Any]) -> Dict[str, str]:
        meta = event.get("meta") or {}
        text = str(event.get("text") or "")
        title = str(event.get("title") or "")
        if event.get("kind") == "conversation":
            actor = str(event.get("actor") or "")
            if actor == "child":
                text = f"孩子说: {text}"
            elif actor == "robot":
                text = f"小智回复: {text}"
            elif actor == "parent":
                text = f"家长播报: {text}"
        elif title and title not in text:
            text = f"{title}: {text}"
        return {
            "type": str(meta.get("type") or self._legacy_type(event)),
            "time": str(event.get("ts") or ""),
            "text": _safe_text(text, 180),
        }

    @staticmethod
    def _legacy_type(event: Dict[str, Any]) -> str:
        if event.get("level") in {"warn", "danger"}:
            return "warn"
        kind = event.get("kind")
        if kind in {"system", "reading", "sleep", "parent_action", "conversation"}:
            return str(kind)
        return "info"

    def _safety_timeline_events(self, date_key: str, limit: int = 50) -> List[Dict[str, Any]]:
        items = []
        for event in self._safety_events(limit=limit):
            if _date_key(event.get("created_at")) != date_key:
                continue
            family = self._event_family(event)
            if family == "fall":
                title = "跌倒检测"
                text = "检测到可能跌倒"
            elif family == "dangerous_item":
                title = "物品提醒"
                text = "发现需关注物品"
            else:
                title = "安全记录"
                text = "记录疑似安全事件"
            confirmed = bool(event.get("confirmed"))
            items.append({
                "id": str(event.get("event_id") or ""),
                "ts": str(event.get("created_at") or ""),
                "date_key": _date_key(event.get("created_at")),
                "kind": "safety",
                "level": "warn" if confirmed else "info",
                "actor": "safety",
                "title": title,
                "text": event.get("summary") or text,
                "meta": {
                    "type": "warn" if confirmed else "info",
                    "family": family,
                    "event_id": event.get("event_id", ""),
                    "confirmed": confirmed,
                },
            })
        return items

    def _reading_timeline_events(self, date_key: str) -> List[Dict[str, Any]]:
        items = []
        for capture in self._reading_capture_items(date_key):
            items.append({
                "id": capture["id"],
                "ts": capture["time_iso"],
                "date_key": date_key,
                "kind": "reading",
                "level": "info",
                "actor": "system",
                "title": "读书拍摄",
                "text": "保存读书拍摄图像",
                "meta": {"type": "reading", "capture_id": capture["id"]},
            })
        return items

    def camera_history(self, date_key: str) -> Dict[str, Any]:
        snapshots = []
        for event in self._safety_events(limit=500):
            if _date_key(event.get("created_at")) != date_key:
                continue
            family = self._event_family(event)
            if family == "fall":
                reason = "跌倒事件" if event.get("confirmed") else "疑似跌倒"
            elif family == "dangerous_item":
                reason = "安全事件" if event.get("confirmed") else "疑似安全"
            else:
                reason = "安全记录"
            annotated = (event.get("paths") or {}).get("annotated") or (event.get("paths") or {}).get("raw")
            if annotated:
                snapshots.append({
                    "time": _time_hm(event.get("created_at")),
                    "path": f"safety/{annotated}",
                    "reason": reason,
                    "event_id": event.get("event_id", ""),
                })
        for capture in self._reading_capture_items(date_key):
            item = {
                "time": capture["time"],
                "path": f"reading/{capture['raw_name']}",
                "reason": "读书拍摄",
            }
            if capture.get("book_pages"):
                item["book_pages"] = capture["book_pages"]
            snapshots.append(item)
        snapshots.sort(key=lambda item: item.get("time", ""), reverse=True)
        return {"date": date_key, "snapshots": snapshots}

    def resolve_history_image(self, encoded_path: str) -> Optional[Path]:
        decoded = unquote(encoded_path or "").lstrip("/")
        if not decoded or ".." in decoded.split("/"):
            return None
        parts = decoded.split("/")
        source = parts[0]
        rel_parts = parts[1:]
        if source == "safety":
            return self._resolve_under(self.safety_record_dir, rel_parts)
        if source == "reading":
            return self._resolve_under(self.reading_capture_dir, rel_parts)
        return None

    def update_sleep_settings(self, body: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            if "bedtime" in body:
                bedtime = str(body.get("bedtime") or "21:00")[:5]
                if len(bedtime) == 5 and bedtime[2] == ":":
                    self._sleep["bedtime"] = bedtime
            if "aid_type" in body:
                aid_type = str(body.get("aid_type") or "whitenoise")
                if aid_type in {"whitenoise", "music", "story"}:
                    self._sleep["aid_type"] = aid_type
            duration_sec = body.get("aid_duration_sec")
            if duration_sec is None and body.get("aid_duration_min") is not None:
                duration_sec = int(body.get("aid_duration_min") or 20) * 60
            if duration_sec is not None:
                duration_sec = max(1, min(int(duration_sec), 7200))
                self._sleep["aid_duration_sec"] = duration_sec
                self._sleep["aid_duration_min"] = max(1, (duration_sec + 59) // 60)
            if "auto_aid" in body:
                self._sleep["auto_aid"] = bool(body.get("auto_aid"))
            if "children" in body:
                self._sleep["children"] = self._clean_children(body.get("children"))
            if "grace_minutes" in body:
                self._sleep["grace_minutes"] = max(0, min(int(body.get("grace_minutes") or 0), 180))
            if "remind_interval_min" in body:
                self._sleep["remind_interval_min"] = max(1, min(int(body.get("remind_interval_min") or 5), 120))
            self._persist_sleep_locked()
        self.add_activity("info", "家长更新睡眠设置", kind="sleep", actor="parent", title="睡眠设置")
        return self.sleep_status()

    def sleep_status(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = now or datetime.now()
        with self._lock:
            data = dict(self._sleep)
        remaining = 0
        if data.get("aid_active"):
            remaining = max(0, int(data.get("aid_ends_at", 0.0) - time.time()))
            if remaining <= 0:
                with self._lock:
                    self._sleep["aid_active"] = False
                    self._sleep["aid_ends_at"] = 0.0
                    self._persist_sleep_locked()
                    data = dict(self._sleep)
        visible_children = self._visible_sleep_children(now)
        state = self._sleep_state(
            data.get("bedtime", "21:00"),
            bool(data.get("aid_active")),
            data.get("children") or [],
            visible_children,
            int(data.get("grace_minutes") or 0),
            now,
        )
        data.pop("aid_ends_at", None)
        data["state"] = state
        data["since"] = _time_hm(now)
        data["aid_remaining_sec"] = remaining if data.get("aid_active") else 0
        data["visible_children"] = visible_children
        data["child_visible"] = bool(visible_children)
        data["presence"] = self._sleep_presence_debug(now, data.get("children") or [], visible_children)
        return data

    def sleep_children(self) -> Dict[str, Any]:
        with self._lock:
            children = list(self._sleep.get("children") or [])
        return {"children": children, "visible_children": self._visible_sleep_children(datetime.now())}

    def update_sleep_presence(
        self,
        body: Dict[str, Any],
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        now = now or datetime.now()
        name = str(body.get("unique_name") or body.get("name") or "").strip()
        visible = bool(body.get("visible", True))
        if not name:
            return {"ok": False, "error": "missing_unique_name", "sleep": self.sleep_status(now=now)}
        with self._lock:
            if visible:
                self._sleep_presence[name] = now.timestamp()
            else:
                self._sleep_presence.pop(name, None)
        return {"ok": True, "sleep": self.sleep_status(now=now)}

    def start_sleep_aid(self, body: Dict[str, Any]) -> Dict[str, Any]:
        self.update_sleep_settings({
            "aid_type": body.get("type") or body.get("aid_type"),
            "aid_duration_sec": body.get("duration_sec"),
            "aid_duration_min": body.get("duration_min"),
        })
        with self._lock:
            duration = int(self._sleep.get("aid_duration_sec") or 1200)
            self._sleep["aid_active"] = True
            self._sleep["aid_ends_at"] = time.time() + duration
            aid_type = self._sleep.get("aid_type", "whitenoise")
            self._persist_sleep_locked()
        self.add_activity("info", f"家长开始助眠: {aid_type}", kind="sleep", actor="parent", title="开始助眠")
        return self.sleep_status()

    def stop_sleep_aid(self) -> Dict[str, Any]:
        with self._lock:
            self._sleep["aid_active"] = False
            self._sleep["aid_ends_at"] = 0.0
            self._persist_sleep_locked()
        self.add_activity("info", "家长停止助眠", kind="sleep", actor="parent", title="停止助眠")
        return self.sleep_status()

    def remind_sleep(self, text: str) -> bool:
        text = _safe_text(text or "宝贝，该准备睡觉啦", 80)
        with self._lock:
            self._sleep["remind_text"] = text
            self._persist_sleep_locked()
        return self.queue_speech(text, source="sleep_remind")

    def request_move(self, direction: str) -> Dict[str, Any]:
        payload = {"direction": str(direction or "stop")}
        busy = self._manual_move_busy_reason(payload["direction"])
        if busy:
            result = {
                "ok": False,
                "direction": payload["direction"],
                "status": "busy",
                "reason": busy,
                "reserved": False,
            }
            self.record_move(payload["direction"], result)
            return result
        result = self._dispatch_move("move", payload)
        self.record_move(payload["direction"], result)
        return result

    def _manual_move_busy_reason(self, direction: str) -> str:
        direction = str(direction or "stop").strip().lower()
        if direction == "stop":
            return ""
        with self._lock:
            if self._mode != "normal":
                return f"mode_{self._mode}"
            if bool(self._person_task_status.get("active")):
                return "person_task_active"
        return ""

    def request_find_child(self, target: str = "nearest", timeout_sec: int = 60) -> Dict[str, Any]:
        if self._person_task_controller:
            result = self.request_person_seek({"target": target, "timeout_sec": timeout_sec})
        else:
            result = self._dispatch_move("find_child", {"target": target})
        self.record_action("家长端请求找孩子")
        return result

    def request_emergency_stop(self) -> Dict[str, Any]:
        result = self._dispatch_move("emergency_stop", {})
        self.record_action("家长端触发急停")
        return result

    def request_person_seek(self, body: Dict[str, Any]) -> Dict[str, Any]:
        target = str(body.get("target") or body.get("unique_name") or "nearest").strip() or "nearest"
        try:
            timeout_sec = int(body.get("timeout_sec") or 60)
        except (TypeError, ValueError):
            timeout_sec = 60
        timeout_sec = max(1, min(timeout_sec, 300))
        if not self._person_task_controller:
            result = {
                "ok": True,
                "reserved": True,
                "action": "seek",
                "target": target,
                "reason": "person_task_controller_not_connected",
            }
            with self._lock:
                self._person_task_status = {
                    "active": False,
                    "action": "seek",
                    "target": target,
                    "started_at": 0.0,
                    "timeout_sec": timeout_sec,
                    "last_result": result,
                }
            return result
        try:
            result = self._person_task_controller.control("seek", target)
        except Exception as exc:
            return {"ok": False, "error": type(exc).__name__, "action": "seek", "target": target}
        if result.get("ok"):
            now = time.time()
            with self._lock:
                self._person_task_status = {
                    "active": True,
                    "action": "seek",
                    "target": target,
                    "started_at": now,
                    "timeout_sec": timeout_sec,
                    "last_result": dict(result),
                }
            self._schedule_person_task_timeout(target, timeout_sec)
            self.add_activity(
                "info",
                f"家长端开始寻找: {target}",
                kind="parent_action",
                actor="parent",
                title="寻找人物",
                meta={"target": target, "timeout_sec": timeout_sec},
            )
        return result

    def request_person_stop(self, reason: str = "dashboard") -> Dict[str, Any]:
        with self._lock:
            target = str(self._person_task_status.get("target") or "nearest")
        self._cancel_person_task_timer()
        if not self._person_task_controller:
            result = {"ok": True, "reserved": True, "action": "stop", "target": target}
        else:
            try:
                result = self._person_task_controller.control("stop", target)
            except Exception as exc:
                result = {"ok": False, "error": type(exc).__name__, "action": "stop", "target": target}
        with self._lock:
            self._person_task_status.update({
                "active": False,
                "action": "stop",
                "target": target,
                "last_result": dict(result),
                "stopped_reason": reason,
            })
        if reason != "timeout":
            self.add_activity(
                "info",
                "家长端停止寻找",
                kind="parent_action",
                actor="parent",
                title="停止寻找",
                meta={"target": target, "reason": reason},
            )
        return result

    def person_task_status(self) -> Dict[str, Any]:
        with self._lock:
            data = dict(self._person_task_status)
        remaining = 0
        if data.get("active"):
            elapsed = time.time() - float(data.get("started_at") or 0.0)
            remaining = max(0, int(float(data.get("timeout_sec") or 0) - elapsed))
        data["remaining_sec"] = remaining
        data["enabled"] = bool(self._person_task_controller)
        return data

    def mark_person_task_done(self, reason: str, event: Optional[Dict[str, Any]] = None) -> None:
        self._cancel_person_task_timer()
        event = dict(event or {})
        with self._lock:
            self._person_task_status.update({
                "active": False,
                "action": "seek",
                "stopped_reason": str(reason or "done"),
                "last_event": event,
            })

    def _schedule_person_task_timeout(self, target: str, timeout_sec: int) -> None:
        self._cancel_person_task_timer()

        def _timeout() -> None:
            with self._lock:
                active = bool(self._person_task_status.get("active"))
                current_target = str(self._person_task_status.get("target") or "")
            if active and current_target == target:
                self.request_person_stop(reason="timeout")

        timer = threading.Timer(float(timeout_sec), _timeout)
        timer.daemon = True
        with self._lock:
            self._person_task_timer = timer
        timer.start()

    def _cancel_person_task_timer(self) -> None:
        with self._lock:
            timer = self._person_task_timer
            self._person_task_timer = None
        if timer:
            timer.cancel()

    def _dispatch_move(self, command: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._move_handler:
            result = {
                "ok": True,
                "reserved": True,
                "command": command,
            }
            result.update(payload)
            return result
        try:
            data = self._move_handler(command, payload)
            return data if isinstance(data, dict) else {"ok": False, "error": "invalid_move_result"}
        except Exception as exc:
            return {
                "ok": False,
                "reserved": True,
                "command": command,
                "error": type(exc).__name__,
            }

    def record_move(self, direction: str, result: Optional[Dict[str, Any]] = None) -> None:
        result = result or {}
        self.add_activity(
            "info",
            f"家长端移动指令: {direction}",
            kind="parent_action",
            actor="parent",
            title="移动控制",
            meta={
                "direction": direction,
                "reserved": bool(result.get("reserved", True)),
                "status": str(result.get("status") or result.get("error") or ""),
            },
        )

    def record_action(self, text: str) -> None:
        self.add_activity("info", text, kind="parent_action", actor="parent", title="家长操作")

    def _safety_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        index = self.safety_record_dir / "index.jsonl"
        if not index.exists():
            return []
        events: List[Dict[str, Any]] = []
        try:
            lines = index.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines[-max(limit * 3, limit) :]:
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    events.append(item)
            except json.JSONDecodeError:
                continue
        events.sort(key=lambda item: _parse_dt(item.get("created_at")) or _MIN_DT, reverse=True)
        return events[:limit]

    @staticmethod
    def _event_family(event: Dict[str, Any]) -> str:
        risk = str(event.get("risk_type") or "")
        candidate = str(event.get("candidate_type") or "")
        if "fall" in risk or "fall" in candidate:
            return "fall"
        if risk in {"sharp_object", "dangerous_item"} or "hazard" in candidate:
            return "dangerous_item"
        return "safety"

    @staticmethod
    def _warn_status(event: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "ok": False,
            "detail": event.get("summary", ""),
            "time": event.get("created_at", ""),
            "event_id": event.get("event_id", ""),
        }

    def _reading_capture_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        if not self.reading_capture_dir.exists():
            return counts
        for path in self.reading_capture_dir.glob("*.json"):
            date_key = self._reading_capture_date(path)
            if date_key:
                counts[date_key] = counts.get(date_key, 0) + 1
        return counts

    def _reading_capture_count(self, date_key: str) -> int:
        return self._reading_capture_counts().get(date_key, 0)

    def _reading_capture_items(self, date_key: str) -> List[Dict[str, Any]]:
        items = []
        if not self.reading_capture_dir.exists():
            return items
        for meta in self.reading_capture_dir.glob("*.json"):
            capture_date = self._reading_capture_date(meta)
            if capture_date != date_key:
                continue
            capture_id = meta.stem
            raw_name = f"{capture_id}_raw.jpg"
            raw_path = self.reading_capture_dir / raw_name
            if not raw_path.exists():
                continue
            dt = _parse_dt(capture_id[:15])
            time_iso = dt.isoformat(timespec="seconds") if dt else meta.stat().st_mtime
            meta_data = self._load_json(meta)
            item = {
                "id": capture_id,
                "raw_name": raw_name,
                "time": _time_hm(dt),
                "time_iso": time_iso if isinstance(time_iso, str) else _now_iso(),
            }
            book_pages = self._book_pages_from_capture_meta(meta_data)
            if book_pages:
                item["book_pages"] = book_pages
            items.append(item)
        items.sort(key=lambda item: item["id"], reverse=True)
        return items

    @staticmethod
    def _load_json(path: Path) -> Dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @classmethod
    def _book_pages_from_capture_meta(cls, meta: Dict[str, Any]) -> List[Dict[str, Any]]:
        detection = meta.get("book_detection") if isinstance(meta, dict) else None
        if not isinstance(detection, dict) or not detection.get("found"):
            return []

        raw_pages = detection.get("pages")
        if not isinstance(raw_pages, list) or not raw_pages:
            raw_pages = [{"corners": detection.get("corners"), "conf": detection.get("confidence")}]

        pages: List[Dict[str, Any]] = []
        for idx, page in enumerate(raw_pages):
            if not isinstance(page, dict):
                continue
            corners = cls._normalize_corners(page.get("corners"))
            if len(corners) < 3:
                continue
            pages.append({
                "index": idx,
                "confidence": cls._float_or_none(page.get("conf")),
                "corners": corners,
            })
        return pages

    @staticmethod
    def _normalize_corners(value: Any) -> List[Dict[str, float]]:
        if not isinstance(value, dict):
            return []
        corners = []
        for name in ("tl", "tr", "br", "bl"):
            raw = value.get(name)
            if not isinstance(raw, (list, tuple)) or len(raw) < 2:
                continue
            try:
                item = {
                    "name": name,
                    "x": float(raw[0]),
                    "y": float(raw[1]),
                }
                if len(raw) >= 3:
                    item["confidence"] = float(raw[2])
                corners.append(item)
            except (TypeError, ValueError):
                continue
        return corners

    @staticmethod
    def _float_or_none(value: Any) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _reading_capture_date(path: Path) -> str:
        stem = path.stem
        dt = _parse_dt(stem[:15])
        return dt.strftime("%Y-%m-%d") if dt else ""

    @staticmethod
    def _clean_children(value: Any) -> List[str]:
        if isinstance(value, str):
            raw_items = value.replace("，", ",").split(",")
        elif isinstance(value, list):
            raw_items = value
        else:
            raw_items = []
        children = []
        seen = set()
        for item in raw_items:
            name = str(item or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            children.append(name[:64])
        return children[:12]

    def _persist_sleep_locked(self) -> None:
        data = dict(self._sleep)
        self._event_store.set_setting("sleep", data)

    def _visible_sleep_children(self, now: datetime) -> List[str]:
        with self._lock:
            children = list(self._sleep.get("children") or [])
            presence = dict(self._sleep_presence)
        cutoff = now.timestamp() - self._sleep_presence_ttl_sec
        visible = []
        for child in children:
            if presence.get(child, 0.0) >= cutoff:
                visible.append(child)
        return visible

    def _sleep_presence_debug(
        self,
        now: datetime,
        children: List[str],
        visible_children: List[str],
    ) -> Dict[str, Any]:
        with self._lock:
            presence = dict(self._sleep_presence)
        now_ts = now.timestamp()
        last_seen = []
        for child in children:
            seen_ts = float(presence.get(child) or 0.0)
            age = max(0, int(round(now_ts - seen_ts))) if seen_ts else None
            last_seen.append({
                "unique_name": child,
                "visible": child in visible_children,
                "age_sec": age,
            })
        return {
            "ttl_sec": int(self._sleep_presence_ttl_sec),
            "configured_children": list(children),
            "visible_children": list(visible_children),
            "last_seen": last_seen,
        }

    @staticmethod
    def _sleep_state(
        bedtime: str,
        aid_active: bool,
        children: List[str],
        visible_children: List[str],
        grace_minutes: int,
        now: datetime,
    ) -> str:
        if aid_active:
            return "sleeping"
        try:
            hour, minute = [int(part) for part in bedtime.split(":", 1)]
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        except Exception:
            return "awake"
        if now < target:
            return "awake"
        if children and not visible_children:
            return "away"
        if now < target + timedelta(minutes=max(0, int(grace_minutes or 0))):
            return "awake"
        return "restless"

    @staticmethod
    def _resolve_under(base: Path, rel_parts: Iterable[str]) -> Optional[Path]:
        base = base.expanduser().resolve()
        candidate = base.joinpath(*rel_parts).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            return None
        return candidate if candidate.exists() and candidate.is_file() else None
