"""agent_client 单元测试 — 本地 stub HTTP 服务验证。运行: python3 arm/test_agent_client.py（在 ~/cloud-model 下）"""
import threading, json
from http.server import BaseHTTPRequestHandler, HTTPServer

from arm import agent_client

_FAKE_JPG = b"\xff\xd8\xff\xe0FAKEJPEG\xff\xd9"


class _Stub(BaseHTTPRequestHandler):
    last_frame_path = None
    last_post_path = None
    frame_status = 200

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/frame.jpg"):
            _Stub.last_frame_path = self.path
            self.send_response(_Stub.frame_status)
            if _Stub.frame_status != 200:
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(_FAKE_JPG)))
            self.end_headers()
            self.wfile.write(_FAKE_JPG)
        elif self.path == "/book/status":
            body = json.dumps({"found": True, "cx": 0.5, "cy": 0.5, "area_ratio": 0.45,
                               "settled": True, "ready": True, "tracking": True}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        _Stub.last_post_path = self.path
        if self.path in (
            "/reading/prepare?timeout=12",
            "/reading/start",
            "/reading/stop",
            "/reading/stop?return_home=1",
        ):
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


def _start_stub():
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_against_stub():
    _Stub.frame_status = 200
    srv = _start_stub()
    port = srv.server_address[1]
    agent_client.ARM_AGENT_URL = f"http://127.0.0.1:{port}"
    assert agent_client.get_frame() == _FAKE_JPG
    assert agent_client.get_frame(wait_ready=True) == _FAKE_JPG
    assert "wait_ready=1&timeout=25" in _Stub.last_frame_path
    st = agent_client.get_status()
    assert st["ready"] is True and st["found"] is True, st
    assert agent_client.prepare_reading() is True
    assert _Stub.last_post_path == "/reading/prepare?timeout=12"
    assert agent_client.start_reading() is True
    assert agent_client.stop_reading() is True
    assert _Stub.last_post_path == "/reading/stop"
    assert agent_client.stop_reading(return_home=True) is True
    assert _Stub.last_post_path == "/reading/stop?return_home=1"
    print("test_against_stub PASS")


def test_unready_frame_returns_none():
    srv = _start_stub()
    port = srv.server_address[1]
    agent_client.ARM_AGENT_URL = f"http://127.0.0.1:{port}"
    _Stub.frame_status = 409
    assert agent_client.get_frame(wait_ready=True) is None
    _Stub.frame_status = 200
    srv.shutdown()
    srv.server_close()
    print("test_unready_frame_returns_none PASS")


def test_agent_down_returns_none():
    agent_client.ARM_AGENT_URL = "http://127.0.0.1:1"   # 无人监听
    assert agent_client.get_frame() is None
    assert agent_client.get_status() is None
    assert agent_client.start_reading() is False
    print("test_agent_down_returns_none PASS")


def test_health_reports_status_and_frame():
    _Stub.frame_status = 200
    srv = _start_stub()
    port = srv.server_address[1]
    agent_client.ARM_AGENT_URL = f"http://127.0.0.1:{port}"
    health = agent_client.health(require_frame=True, timeout=1.0)
    assert health["ok"] is True, health
    assert health["status_ok"] is True, health
    assert health["frame_ok"] is True, health
    assert health["status"]["ready"] is True, health
    srv.shutdown()
    srv.server_close()
    print("test_health_reports_status_and_frame PASS")


def test_health_marks_missing_frame_unhealthy():
    srv = _start_stub()
    port = srv.server_address[1]
    agent_client.ARM_AGENT_URL = f"http://127.0.0.1:{port}"
    _Stub.frame_status = 409
    health = agent_client.health(require_frame=True, timeout=1.0)
    assert health["ok"] is False, health
    assert health["status_ok"] is True, health
    assert health["frame_ok"] is False, health
    _Stub.frame_status = 200
    srv.shutdown()
    srv.server_close()
    print("test_health_marks_missing_frame_unhealthy PASS")


if __name__ == "__main__":
    test_against_stub()
    test_unready_frame_returns_none()
    test_agent_down_returns_none()
    test_health_reports_status_and_frame()
    test_health_marks_missing_frame_unhealthy()
    print("ALL PASS")
