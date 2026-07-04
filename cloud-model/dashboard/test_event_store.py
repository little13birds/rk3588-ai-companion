"""Tests for the dashboard SQLite event store.

Run from repo root:
    python3 -m dashboard.test_event_store
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from dashboard.event_store import DashboardEventStore


def _db_path() -> Path:
    return Path(tempfile.mkdtemp()) / "dashboard.db"


def test_event_store_persists_and_lists_old_to_new():
    db = _db_path()
    store = DashboardEventStore(str(db))
    first = store.append(
        kind="conversation",
        level="info",
        actor="child",
        title="孩子",
        text="你好小智",
        ts="2026-06-22T10:00:00+08:00",
        meta={"type": "child"},
    )
    second = store.append(
        kind="system",
        level="info",
        actor="system",
        title="模式切换",
        text="进入讲故事模式",
        ts="2026-06-22T10:01:00+08:00",
        meta={"mode": "story"},
    )

    reopened = DashboardEventStore(str(db))
    events = reopened.list_events(date_key="2026-06-22", limit=10)

    assert [item["id"] for item in events] == [first["id"], second["id"]]
    assert events[0]["kind"] == "conversation"
    assert events[0]["meta"]["type"] == "child"
    assert events[1]["text"] == "进入讲故事模式"
    assert events[1]["date_key"] == "2026-06-22"


def test_event_store_filters_by_kind_and_limit():
    store = DashboardEventStore(str(_db_path()))
    store.append(
        kind="conversation",
        level="info",
        actor="child",
        title="孩子",
        text="第一句",
        ts="2026-06-22T10:00:00+08:00",
    )
    store.append(
        kind="activity",
        level="warn",
        actor="system",
        title="提醒",
        text="光线偏暗",
        ts="2026-06-22T10:01:00+08:00",
    )
    store.append(
        kind="conversation",
        level="info",
        actor="robot",
        title="小智",
        text="第二句",
        ts="2026-06-22T10:02:00+08:00",
    )

    events = store.list_events(
        date_key="2026-06-22",
        kinds=["conversation"],
        limit=1,
    )

    assert len(events) == 1
    assert events[0]["actor"] == "robot"
    assert events[0]["text"] == "第二句"


def test_event_store_persists_json_settings():
    db = _db_path()
    store = DashboardEventStore(str(db))
    store.set_setting("sleep", {"bedtime": "21:30", "children": ["tao"]})
    store.close()

    reopened = DashboardEventStore(str(db))
    value = reopened.get_setting("sleep")
    assert value["bedtime"] == "21:30"
    assert value["children"] == ["tao"]


if __name__ == "__main__":
    test_event_store_persists_and_lists_old_to_new()
    print("test_event_store_persists_and_lists_old_to_new PASS")
    test_event_store_filters_by_kind_and_limit()
    print("test_event_store_filters_by_kind_and_limit PASS")
    test_event_store_persists_json_settings()
    print("test_event_store_persists_json_settings PASS")
    print("ALL PASS")
