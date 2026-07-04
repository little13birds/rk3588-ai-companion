"""SQLite-backed event store for parent-dashboard records."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


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
        return None


def _date_key(value: Any) -> str:
    dt = _parse_dt(value)
    if dt:
        return dt.strftime("%Y-%m-%d")
    return _now_iso()[:10]


def _event_id(ts: str) -> str:
    dt = _parse_dt(ts) or datetime.now().astimezone()
    return "{}_{}".format(dt.strftime("%Y%m%dT%H%M%S_%f"), uuid.uuid4().hex[:8])


class DashboardEventStore:
    """Small SQLite event store used by DashboardState.

    The store keeps one connection guarded by a lock because DashboardState is
    accessed by the main loop and the HTTP server thread.
    """

    def __init__(self, db_path: str = "dashboard_records/dashboard.db"):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path),
            timeout=5.0,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA busy_timeout=3000")
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dashboard_events (
                    id TEXT PRIMARY KEY,
                    ts TEXT NOT NULL,
                    date_key TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    level TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL DEFAULT '',
                    meta_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_dashboard_events_date_ts
                ON dashboard_events(date_key, ts, id)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_dashboard_events_kind_ts
                ON dashboard_events(kind, ts, id)
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dashboard_settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.commit()

    def append(
        self,
        *,
        kind: str,
        level: str,
        actor: str,
        title: str,
        text: str,
        ts: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
        event_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        ts = ts or _now_iso()
        item = {
            "id": event_id or _event_id(ts),
            "ts": ts,
            "date_key": _date_key(ts),
            "kind": str(kind or "activity"),
            "level": str(level or "info"),
            "actor": str(actor or "system"),
            "title": str(title or ""),
            "text": str(text or ""),
            "meta": dict(meta or {}),
        }
        meta_json = json.dumps(item["meta"], ensure_ascii=False, sort_keys=True)
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO dashboard_events
                (id, ts, date_key, kind, level, actor, title, text, meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    item["ts"],
                    item["date_key"],
                    item["kind"],
                    item["level"],
                    item["actor"],
                    item["title"],
                    item["text"],
                    meta_json,
                ),
            )
            self._conn.commit()
        return item

    def list_events(
        self,
        *,
        date_key: Optional[str] = None,
        kinds: Optional[Iterable[str]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 100), 500))
        clauses: List[str] = []
        params: List[Any] = []
        if date_key:
            clauses.append("date_key = ?")
            params.append(str(date_key))
        kinds_list = [str(kind) for kind in (kinds or []) if str(kind)]
        if kinds_list:
            placeholders = ",".join("?" for _ in kinds_list)
            clauses.append(f"kind IN ({placeholders})")
            params.extend(kinds_list)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT rowid AS seq, id, ts, date_key, kind, level, actor, title, text, meta_json
                FROM dashboard_events
                {where}
                ORDER BY ts DESC, rowid DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        events = [self._row_to_event(row) for row in reversed(rows)]
        return events

    def set_setting(self, key: str, value: Dict[str, Any]) -> None:
        item_key = str(key or "").strip()
        if not item_key:
            return
        raw = json.dumps(dict(value or {}), ensure_ascii=False, sort_keys=True)
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO dashboard_settings (key, value_json, updated_at)
                VALUES (?, ?, ?)
                """,
                (item_key, raw, _now_iso()),
            )
            self._conn.commit()

    def get_setting(self, key: str, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        item_key = str(key or "").strip()
        if not item_key:
            return dict(default or {})
        with self._lock:
            row = self._conn.execute(
                "SELECT value_json FROM dashboard_settings WHERE key = ?",
                (item_key,),
            ).fetchone()
        if not row:
            return dict(default or {})
        try:
            value = json.loads(row["value_json"] or "{}")
            return value if isinstance(value, dict) else dict(default or {})
        except json.JSONDecodeError:
            return dict(default or {})

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> Dict[str, Any]:
        try:
            meta = json.loads(row["meta_json"] or "{}")
            if not isinstance(meta, dict):
                meta = {}
        except json.JSONDecodeError:
            meta = {}
        return {
            "id": row["id"],
            "seq": row["seq"],
            "ts": row["ts"],
            "date_key": row["date_key"],
            "kind": row["kind"],
            "level": row["level"],
            "actor": row["actor"],
            "title": row["title"],
            "text": row["text"],
            "meta": meta,
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
