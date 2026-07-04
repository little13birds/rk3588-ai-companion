"""Tests for dashboard state adapters.

Run from ~/cloud-model with: python3 -m dashboard.test_state
"""
import json
import sys
import tempfile
import types
from datetime import datetime, timedelta
from pathlib import Path

from dashboard.state import DashboardState


def _make_state():
    root = Path(tempfile.mkdtemp())
    safety = root / "safety_records"
    reading = root / "reading_captures"
    safety.mkdir()
    reading.mkdir()
    return DashboardState(
        safety_record_dir=str(safety),
        reading_capture_dir=str(reading),
        event_db_path=str(root / "dashboard_records" / "dashboard.db"),
        safety_active_sec=3600,
        alert_window_sec=3600,
    ), safety, reading


def test_safety_status_and_history():
    state, safety, _reading = _make_state()
    today = datetime.now().strftime("%Y-%m-%d")
    now_iso = datetime.now().astimezone().isoformat(timespec="seconds")
    event_dir = safety / today / "e1"
    event_dir.mkdir(parents=True)
    (event_dir / "annotated.jpg").write_bytes(b"jpg")
    item = {
        "event_id": "e1",
        "created_at": now_iso,
        "candidate_type": "hazard_candidate",
        "confirmed": True,
        "severity": "high",
        "risk_type": "sharp_object",
        "summary": "hand near sharp object",
        "paths": {"annotated": f"{today}/e1/annotated.jpg"},
    }
    (safety / "index.jsonl").write_text(json.dumps(item, ensure_ascii=False) + "\n", encoding="utf-8")

    status = state.safety_status()
    assert status["dangerous_item"]["ok"] is False
    assert status["fall"]["ok"] is True

    history = state.camera_history(today)
    assert history["snapshots"][0]["reason"] == "安全事件"
    path = state.resolve_history_image(history["snapshots"][0]["path"])
    assert path and path.read_bytes() == b"jpg"



def test_camera_snapshot_uses_configured_provider():
    state, _safety, _reading = _make_state()
    state.set_camera_snapshot_provider(lambda: b"platform-camera-jpeg")
    assert state.camera_snapshot() == b"platform-camera-jpeg"


def test_camera_snapshot_switches_to_reading_provider_in_reading_mode():
    state, _safety, _reading = _make_state()
    state.set_camera_snapshot_provider(lambda: b"platform-camera-jpeg")
    state.set_reading_camera_snapshot_provider(lambda: b"reading-arm-jpeg")

    assert state.camera_snapshot() == b"platform-camera-jpeg"
    assert state.camera_source()["source"] == "platform_camera"

    state.set_runtime(mode="reading")
    assert state.camera_snapshot() == b"reading-arm-jpeg"
    source = state.camera_source()
    assert source["mode"] == "reading"
    assert source["source"] == "reading_arm"


def test_sleep_and_speech_queue():
    state, _safety, _reading = _make_state()
    state.update_sleep_settings({
        "bedtime": "21:30",
        "aid_type": "music",
        "aid_duration_sec": 3,
        "auto_aid": False,
    })
    assert state.sleep_status()["aid_type"] == "music"
    state.start_sleep_aid({"type": "music", "duration_sec": 3})
    assert state.sleep_status()["aid_active"] is True
    assert state.remind_sleep("该睡觉啦") is True
    req = state.pop_speech_request()
    assert req["text"] == "该睡觉啦"
    state.complete_speech_request()


def test_reading_capture_records():
    state, _safety, reading = _make_state()
    (reading / "20260620T193246_123.json").write_text("{}", encoding="utf-8")
    (reading / "20260620T193246_123_raw.jpg").write_bytes(b"raw")
    history = state.camera_history("2026-06-20")
    assert history["snapshots"][0]["reason"] == "读书拍摄"


def test_reading_capture_records_include_book_page_corners():
    state, _safety, reading = _make_state()
    (reading / "20260620T193246_123.json").write_text(
        json.dumps({
            "book_detection": {
                "found": True,
                "pages": [{
                    "conf": 0.91,
                    "corners": {
                        "tl": [10, 20, 0.9],
                        "tr": [110, 20, 0.88],
                        "br": [115, 160, 0.87],
                        "bl": [8, 158, 0.86],
                    },
                }],
            },
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (reading / "20260620T193246_123_raw.jpg").write_bytes(b"raw")

    snapshot = state.camera_history("2026-06-20")["snapshots"][0]

    assert snapshot["reason"] == "读书拍摄"
    assert snapshot["book_pages"][0]["corners"][0]["name"] == "tl"
    assert snapshot["book_pages"][0]["corners"][0]["x"] == 10.0
    assert snapshot["book_pages"][0]["corners"][0]["confidence"] == 0.9


def test_environment_first_request_reads_sensor_before_returning_zero():
    sensors_pkg = types.ModuleType("sensors")
    sensors_mod = types.ModuleType("sensors.sensors")
    sensors_mod.read_temperature = lambda: {"temperature": 27.5, "humidity": 62.0}
    sensors_mod.read_light = lambda: 418.0
    old_pkg = sys.modules.get("sensors")
    old_mod = sys.modules.get("sensors.sensors")
    sys.modules["sensors"] = sensors_pkg
    sys.modules["sensors.sensors"] = sensors_mod
    try:
        state, _safety, _reading = _make_state()
        data = state.environment()
    finally:
        if old_pkg is None:
            sys.modules.pop("sensors", None)
        else:
            sys.modules["sensors"] = old_pkg
        if old_mod is None:
            sys.modules.pop("sensors.sensors", None)
        else:
            sys.modules["sensors.sensors"] = old_mod

    assert data["temperature"] == 27.5
    assert data["humidity"] == 62.0
    assert data["light"] == 418.0
    assert data["errors"] == []


def test_system_status_provider():
    state, _safety, _reading = _make_state()
    state.set_scheduler_status_provider(lambda: {
        "enabled": True,
        "mode": "reading",
        "leases": [{"resource": "npu_book"}],
        "resources": {"npu_book": {"leases": [{"owner": "mode.reading"}]}},
        "conflicts": [{"resource": "npu_safety", "blocked_by": "mode.reading"}],
    })
    assert state.system_mode()["mode"] == "reading"
    assert state.system_resources()["resources"]["npu_book"]["leases"][0]["owner"] == "mode.reading"
    assert state.system_conflicts()["conflicts"][0]["blocked_by"] == "mode.reading"


def test_dashboard_move_handler_returns_backend_result():
    state, _safety, _reading = _make_state()
    calls = []

    def handler(command, payload):
        calls.append((command, dict(payload)))
        return {"ok": True, "reserved": False, "direction": payload.get("direction"), "status": "published"}

    state.set_move_handler(handler)
    result = state.request_move("forward")
    assert result["ok"] is True
    assert result["reserved"] is False
    assert result["direction"] == "forward"
    assert calls == [("move", {"direction": "forward"})]

    features = state.system_features()
    assert features["movement"]["reserved"] is False
    print("test_dashboard_move_handler_returns_backend_result PASS")


def test_dashboard_move_blocks_manual_directions_while_runtime_busy_but_allows_stop():
    state, _safety, _reading = _make_state()
    calls = []

    def handler(command, payload):
        calls.append((command, dict(payload)))
        return {"ok": True, "reserved": False, "direction": payload.get("direction"), "status": "published"}

    state.set_move_handler(handler)
    state.set_runtime(mode="reading")

    blocked = state.request_move("forward")
    stopped = state.request_move("stop")

    assert blocked["ok"] is False
    assert blocked["status"] == "busy"
    assert blocked["reason"] == "mode_reading"
    assert stopped["ok"] is True
    assert calls == [("move", {"direction": "stop"})]
    print("test_dashboard_move_blocks_manual_directions_while_runtime_busy_but_allows_stop PASS")


def test_dashboard_move_blocks_manual_directions_while_person_task_active():
    state, _safety, _reading = _make_state()
    calls = []

    state.set_move_handler(lambda command, payload: (calls.append((command, dict(payload))) or {"ok": True}))
    state._person_task_status.update({"active": True, "action": "seek", "target": "tao"})

    result = state.request_move("left")
    features = state.system_features()

    assert result["ok"] is False
    assert result["status"] == "busy"
    assert result["reason"] == "person_task_active"
    assert calls == []
    assert features["movement"]["manual_allowed"] is False
    assert "人物任务" in features["movement"]["detail"]
    print("test_dashboard_move_blocks_manual_directions_while_person_task_active PASS")


def test_sleep_restless_requires_configured_visible_child_after_grace():
    state, _safety, _reading = _make_state()
    now = datetime.now()
    bedtime = (now - timedelta(minutes=20)).strftime("%H:%M")

    state.update_sleep_settings({
        "bedtime": bedtime,
        "children": ["alice"],
        "grace_minutes": 10,
    })
    no_child = state.sleep_status(now=now)
    assert no_child["state"] == "away"
    assert no_child["child_visible"] is False

    state.update_sleep_presence({"unique_name": "bob", "visible": True}, now=now)
    wrong_child = state.sleep_status(now=now)
    assert wrong_child["state"] == "away"

    state.update_sleep_presence({"unique_name": "alice", "visible": True}, now=now)
    visible_child = state.sleep_status(now=now)
    assert visible_child["state"] == "restless"
    assert visible_child["child_visible"] is True
    assert visible_child["visible_children"] == ["alice"]
    print("test_sleep_restless_requires_configured_visible_child_after_grace PASS")


def test_sleep_status_includes_presence_debug_metadata():
    state, _safety, _reading = _make_state()
    now = datetime.now()
    state.update_sleep_settings({
        "bedtime": (now - timedelta(minutes=20)).strftime("%H:%M"),
        "children": ["alice", "bob"],
        "grace_minutes": 0,
    })
    state.update_sleep_presence({"unique_name": "alice", "visible": True}, now=now)

    status = state.sleep_status(now=now + timedelta(seconds=5))
    presence = status["presence"]
    assert presence["ttl_sec"] == 30
    assert presence["configured_children"] == ["alice", "bob"]
    assert presence["visible_children"] == ["alice"]
    alice = next(item for item in presence["last_seen"] if item["unique_name"] == "alice")
    bob = next(item for item in presence["last_seen"] if item["unique_name"] == "bob")
    assert alice["visible"] is True
    assert 4 <= alice["age_sec"] <= 6
    assert bob["visible"] is False
    assert bob["age_sec"] is None
    print("test_sleep_status_includes_presence_debug_metadata PASS")


def test_sleep_settings_persist_in_sqlite():
    root = Path(tempfile.mkdtemp())
    safety = root / "safety_records"
    reading = root / "reading_captures"
    event_db = root / "dashboard_records" / "dashboard.db"
    safety.mkdir()
    reading.mkdir()

    state = DashboardState(
        safety_record_dir=str(safety),
        reading_capture_dir=str(reading),
        event_db_path=str(event_db),
    )
    state.update_sleep_settings({"bedtime": "22:10", "children": ["alice", "bob"]})

    reopened = DashboardState(
        safety_record_dir=str(safety),
        reading_capture_dir=str(reading),
        event_db_path=str(event_db),
    )
    status = reopened.sleep_status()
    assert status["bedtime"] == "22:10"
    assert status["children"] == ["alice", "bob"]
    print("test_sleep_settings_persist_in_sqlite PASS")


def test_dashboard_events_persist_to_sqlite():
    root = Path(tempfile.mkdtemp())
    safety = root / "safety_records"
    reading = root / "reading_captures"
    event_db = root / "dashboard_records" / "dashboard.db"
    safety.mkdir()
    reading.mkdir()
    now_iso = datetime.now().astimezone().isoformat(timespec="seconds")
    today = datetime.now().strftime("%Y-%m-%d")

    state = DashboardState(
        safety_record_dir=str(safety),
        reading_capture_dir=str(reading),
        event_db_path=str(event_db),
        safety_active_sec=3600,
        alert_window_sec=3600,
    )
    state.add_conversation("child", "你好小智", when=now_iso)
    state.add_conversation("robot", "我在，请说", when=now_iso)
    state.add_activity("info", "孩子发起语音交互", when=now_iso)
    state.set_runtime(mode="story")
    assert state.queue_speech("宝贝，该准备睡觉啦", source="sleep_remind") is True

    reopened = DashboardState(
        safety_record_dir=str(safety),
        reading_capture_dir=str(reading),
        event_db_path=str(event_db),
        safety_active_sec=3600,
        alert_window_sec=3600,
    )

    conversation = reopened.conversation_summary()
    assert [item["text"] for item in conversation[:2]] == ["你好小智", "我在，请说"]
    assert any(item["type"] == "parent" and item["source"] == "sleep_remind"
               for item in conversation)

    events = reopened.dashboard_timeline(date_key=today, limit=20)
    assert [item["text"] for item in events[:3]] == [
        "你好小智",
        "我在，请说",
        "孩子发起语音交互",
    ]
    assert any(item["kind"] == "system" and item["meta"].get("mode") == "story"
               for item in events)
    assert any(item["kind"] == "sleep" and item["actor"] == "parent"
               for item in events)


if __name__ == "__main__":
    test_safety_status_and_history()
    print("test_safety_status_and_history PASS")
    test_camera_snapshot_uses_configured_provider()
    print("test_camera_snapshot_uses_configured_provider PASS")
    test_camera_snapshot_switches_to_reading_provider_in_reading_mode()
    print("test_camera_snapshot_switches_to_reading_provider_in_reading_mode PASS")
    test_sleep_and_speech_queue()
    print("test_sleep_and_speech_queue PASS")
    test_reading_capture_records()
    print("test_reading_capture_records PASS")
    test_reading_capture_records_include_book_page_corners()
    print("test_reading_capture_records_include_book_page_corners PASS")
    test_environment_first_request_reads_sensor_before_returning_zero()
    print("test_environment_first_request_reads_sensor_before_returning_zero PASS")
    test_system_status_provider()
    print("test_system_status_provider PASS")
    test_dashboard_move_handler_returns_backend_result()
    test_dashboard_move_blocks_manual_directions_while_runtime_busy_but_allows_stop()
    test_dashboard_move_blocks_manual_directions_while_person_task_active()
    test_sleep_restless_requires_configured_visible_child_after_grace()
    test_sleep_status_includes_presence_debug_metadata()
    test_sleep_settings_persist_in_sqlite()
    test_dashboard_events_persist_to_sqlite()
    print("test_dashboard_events_persist_to_sqlite PASS")
    print("ALL PASS")
