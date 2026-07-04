"""Dashboard people HTTP endpoint tests."""

import base64
import json
import time
import urllib.error
import urllib.request

from dashboard.server import DashboardServer
from dashboard.test_people import FakePeopleClient
from dashboard.test_state import _make_state


def _get_json(port: int, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2.0) as resp:
        assert resp.status == 200
        return json.loads(resp.read().decode("utf-8"))


def _get_bytes(port: int, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2.0) as resp:
        assert resp.status == 200
        return resp.read(), resp.headers


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


class FakePersonTaskController:
    def __init__(self):
        self.calls = []

    def control(self, action, target):
        self.calls.append((action, target))
        return {"ok": True, "action": action, "target": target}


def test_people_registry_upload_enroll_and_delete_endpoints():
    state, _safety, _reading = _make_state()
    people = FakePeopleClient()
    state.set_people_client(people)
    server = DashboardServer(state, host="127.0.0.1", port=0)
    assert server.start()
    try:
        registry = _get_json(server.port, "/api/people")
        assert [p["unique_name"] for p in registry["people"]] == ["tao", "xiao"]

        upload = _post_json(
            server.port,
            "/api/people/candidates/upload",
            {"image_b64": base64.b64encode(b"jpg-bytes").decode("ascii")},
        )
        assert upload["known_faces"][0]["unique_name"] == "tao"
        candidate = upload["candidates"][0]
        assert "embedding" not in candidate

        enrolled = _post_json(
            server.port,
            "/api/people/enroll",
            {"candidate_id": candidate["candidate_id"], "unique_name": "xiao"},
        )
        assert enrolled["ok"] is True
        assert people.enrolled[0]["unique_name"] == "xiao"

        deleted = _post_json(server.port, "/api/people/delete", {"unique_name": "tao"})
        assert deleted["ok"] is True
        assert people.deleted == ["tao"]
    finally:
        server.stop()


def test_people_capture_endpoint_uses_platform_snapshot_provider():
    state, _safety, _reading = _make_state()
    people = FakePeopleClient()
    state.set_people_client(people)
    state.set_camera_snapshot_provider(lambda: b"jpg-bytes")
    server = DashboardServer(state, host="127.0.0.1", port=0)
    assert server.start()
    try:
        capture = _post_json(server.port, "/api/people/candidates/capture")
        assert capture["ok"] is True
        assert capture["candidates"][0]["source"] == "platform_camera"
    finally:
        server.stop()


def test_camera_snapshot_endpoint_reports_reading_source():
    state, _safety, _reading = _make_state()
    state.set_camera_snapshot_provider(lambda: b"platform-jpg")
    state.set_reading_camera_snapshot_provider(lambda: b"reading-jpg")
    server = DashboardServer(state, host="127.0.0.1", port=0)
    assert server.start()
    try:
        source = _get_json(server.port, "/api/camera/source")
        assert source["source"] == "platform_camera"

        jpg, headers = _get_bytes(server.port, "/api/camera/snapshot")
        assert jpg == b"platform-jpg"
        assert headers["X-Camera-Source"] == "platform_camera"

        state.set_runtime(mode="reading")
        source = _get_json(server.port, "/api/camera/source")
        assert source["source"] == "reading_arm"

        jpg, headers = _get_bytes(server.port, "/api/camera/snapshot")
        assert jpg == b"reading-jpg"
        assert headers["X-Camera-Source"] == "reading_arm"
    finally:
        server.stop()


def test_person_task_seek_stop_status_and_timeout():
    state, _safety, _reading = _make_state()
    controller = FakePersonTaskController()
    state.set_person_task_controller(controller)
    server = DashboardServer(state, host="127.0.0.1", port=0)
    assert server.start()
    try:
        seek = _post_json(server.port, "/api/person-task/seek", {"target": "tao", "timeout_sec": 1})
        assert seek["ok"] is True
        assert controller.calls[-1] == ("seek", "tao")

        status = _get_json(server.port, "/api/person-task/status")
        assert status["active"] is True
        assert status["target"] == "tao"
        assert status["remaining_sec"] <= 1

        stopped = _post_json(server.port, "/api/person-task/stop")
        assert stopped["ok"] is True
        assert controller.calls[-1] == ("stop", "tao")
        assert _get_json(server.port, "/api/person-task/status")["active"] is False

        _post_json(server.port, "/api/person-task/seek", {"target": "xiao", "timeout_sec": 1})
        time.sleep(1.2)
        assert controller.calls[-1] == ("stop", "xiao")
    finally:
        server.stop()


if __name__ == "__main__":
    test_people_registry_upload_enroll_and_delete_endpoints()
    test_people_capture_endpoint_uses_platform_snapshot_provider()
    test_camera_snapshot_endpoint_reports_reading_source()
    test_person_task_seek_stop_status_and_timeout()
    print("ALL PASS")
