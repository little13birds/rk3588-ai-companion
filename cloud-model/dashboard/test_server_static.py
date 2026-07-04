"""Dashboard HTTP static resource tests.

Run from ~/cloud-model with: python3 -m dashboard.test_server_static
"""
import urllib.request

from dashboard.server import DashboardServer
from dashboard.state import DashboardState


def test_client_state_js_is_served():
    state = DashboardState()
    server = DashboardServer(state, host="127.0.0.1", port=0)
    assert server.start() is True
    try:
      url = f"http://127.0.0.1:{server.port}/client_state.js"
      with urllib.request.urlopen(url, timeout=2) as resp:
          body = resp.read().decode("utf-8")
          content_type = resp.headers.get("Content-Type", "")
      assert resp.status == 200
      assert "javascript" in content_type
      assert "DashboardClientState" in body
    finally:
      server.stop()


def test_control_page_contains_camera_stream_and_existing_feature_hooks():
    state = DashboardState()
    server = DashboardServer(state, host="127.0.0.1", port=0)
    assert server.start() is True
    try:
      url = f"http://127.0.0.1:{server.port}/parent-dashboard.html"
      with urllib.request.urlopen(url, timeout=2) as resp:
          body = resp.read().decode("utf-8")
      assert resp.status == 200
      assert 'id="cam-img-control"' in body
      assert "refreshCameraControl" in body
      assert "ENDPOINTS.systemResources" in body
      assert "ENDPOINTS.systemConflicts" in body
      assert "ENDPOINTS.sleepChildren" in body
      assert "movementManualAllowed" in body
      assert "setMovementButtonsEnabled" in body
      assert "applyFindChildTaskStatus" in body
      assert "closeFindChildModal" in body
      assert "stopped_reason === 'arrived'" in body
    finally:
      server.stop()


if __name__ == "__main__":
    test_client_state_js_is_served()
    test_control_page_contains_camera_stream_and_existing_feature_hooks()
    print("test_client_state_js_is_served PASS")
