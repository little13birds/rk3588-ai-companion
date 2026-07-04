"""Dashboard system endpoint sample. Run: python3 -m dashboard.test_server_system"""
import json
import socket
import urllib.request
from pathlib import Path

from dashboard.server import DashboardServer
from dashboard.test_state import _make_state


def _get_json(port: int, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2.0) as resp:
        assert resp.status == 200
        return json.loads(resp.read().decode("utf-8"))


def _post_json(port: int, path: str, data=None):
    raw = json.dumps(data or {}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=raw,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=2.0) as resp:
        assert resp.status == 200
        return json.loads(resp.read().decode("utf-8"))


def test_system_endpoints():
    state, _safety, _reading = _make_state()
    state.set_scheduler_status_provider(lambda: {
        "enabled": True,
        "mode": "reading",
        "leases": [{"resource": "npu_book"}],
        "resources": {"npu_book": {"leases": [{"owner": "mode.reading"}]}},
        "conflicts": [{"resource": "npu_safety", "blocked_by": "mode.reading"}],
    })
    server = DashboardServer(state, host="127.0.0.1", port=0)
    assert server.start()
    try:
        assert _get_json(server.port, "/api/system/mode")["mode"] == "reading"
        assert _get_json(server.port, "/api/system/resources")["leases"][0]["resource"] == "npu_book"
        assert _get_json(server.port, "/api/system/conflicts")["conflicts"][0]["blocked_by"] == "mode.reading"
        assert _get_json(server.port, "/api/system/features")["movement"]["reserved"] is True
    finally:
        server.stop()
    print("test_system_endpoints PASS")


def test_dashboard_timeline_endpoints():
    state, _safety, _reading = _make_state()
    state.add_conversation(
        "child",
        "你好小智",
        when="2026-06-22T10:00:00+08:00",
    )
    state.add_activity(
        "system",
        "模式切换为 story",
        when="2026-06-22T10:01:00+08:00",
        kind="system",
        title="模式切换",
        meta={"mode": "story"},
    )
    server = DashboardServer(state, host="127.0.0.1", port=0)
    assert server.start()
    try:
        events = _get_json(server.port, "/api/dashboard/events?date=2026-06-22")
        assert events[0]["kind"] == "conversation"
        assert events[0]["text"] == "你好小智"

        timeline = _get_json(server.port, "/api/dashboard/timeline?date=2026-06-22")
        assert [item["kind"] for item in timeline[:2]] == ["conversation", "system"]
        assert timeline[1]["meta"]["mode"] == "story"
    finally:
        server.stop()
    print("test_dashboard_timeline_endpoints PASS")


def test_move_and_sleep_endpoints():
    state, _safety, _reading = _make_state()
    calls = []

    def move_handler(command, payload):
        calls.append((command, dict(payload)))
        return {"ok": True, "reserved": False, "command": command, "direction": payload.get("direction", "stop")}

    state.set_move_handler(move_handler, lambda: {"enabled": True, "reserved": False})
    server = DashboardServer(state, host="127.0.0.1", port=0)
    assert server.start()
    try:
        move = _post_json(server.port, "/api/move", {"direction": "forward"})
        assert move["reserved"] is False
        assert calls[0] == ("move", {"direction": "forward"})

        _post_json(server.port, "/api/sleep/settings", {"children": ["alice"], "grace_minutes": 3})
        children = _get_json(server.port, "/api/sleep/children")
        assert children["children"] == ["alice"]
        presence = _post_json(server.port, "/api/sleep/presence", {"unique_name": "alice", "visible": True})
        assert presence["ok"] is True
        assert "alice" in presence["sleep"]["visible_children"]
    finally:
        server.stop()
    print("test_move_and_sleep_endpoints PASS")


def test_camera_snapshot_client_disconnect_is_quiet():
    state, _safety, _reading = _make_state()
    state.set_camera_snapshot_provider(lambda: b"x" * 200000)
    server = DashboardServer(state, host="127.0.0.1", port=0)
    assert server.start()
    try:
        sock = socket.create_connection(("127.0.0.1", server.port), timeout=2.0)
        sock.sendall(b"GET /api/camera/snapshot HTTP/1.1\r\nHost: localhost\r\n\r\n")
        sock.close()
    finally:
        server.stop()
    print("test_camera_snapshot_client_disconnect_is_quiet PASS")


def test_server_handles_client_disconnect_without_error_response_retry():
    source = Path(__file__).resolve().with_name("server.py").read_text(encoding="utf-8")
    assert "except (BrokenPipeError, ConnectionResetError)" in source
    assert "event=client_disconnected" in source
    assert source.index("except (BrokenPipeError, ConnectionResetError)") < source.index("except Exception as exc:")
    print("test_server_handles_client_disconnect_without_error_response_retry PASS")


if __name__ == "__main__":
    test_system_endpoints()
    test_dashboard_timeline_endpoints()
    test_move_and_sleep_endpoints()
    test_camera_snapshot_client_disconnect_is_quiet()
    test_server_handles_client_disconnect_without_error_response_retry()
    print("ALL PASS")
