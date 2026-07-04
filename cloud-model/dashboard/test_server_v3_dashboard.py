"""Dashboard v3 frontend compatibility tests.

Run from ~/cloud-model-safety-mainline with:
python3 -m dashboard.test_server_v3_dashboard
"""
import json
from pathlib import Path
import urllib.request

from dashboard.server import DashboardServer
from dashboard.state import DashboardState

_DASHBOARD_DIR = Path(__file__).resolve().parent


def _get(port: int, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10.0) as resp:
        body = resp.read()
        return resp.status, resp.headers, body


def _get_json(port: int, path: str):
    status, _headers, body = _get(port, path)
    assert status == 200
    return json.loads(body.decode("utf-8"))


def test_v3_dashboard_static_entry_and_assets_are_served():
    state = DashboardState()
    server = DashboardServer(state, host="127.0.0.1", port=0)
    assert server.start() is True
    try:
        status, headers, body = _get(server.port, "/")
        root_html = body.decode("utf-8")
        assert status == 200
        assert "text/html" in headers.get("Content-Type", "")
        assert 'id="historyFilterDropdown"' in root_html
        assert "/v3-dashboard/css/base.css" in root_html

        status, headers, body = _get(server.port, "/v3-dashboard/")
        html = body.decode("utf-8")
        assert status == 200
        assert "text/html" in headers.get("Content-Type", "")
        assert 'id="historyFilterDropdown"' in html
        assert 'id="historyFilterTypes"' in html
        assert "/v3-dashboard/css/base.css" in html
        assert "/v3-dashboard/js/app.js" in html

        status, headers, body = _get(server.port, "/v3-dashboard/js/app.js")
        assert status == 200
        assert "javascript" in headers.get("Content-Type", "")
        assert b"selectedHistoryCategories" in body

        status, headers, body = _get(server.port, "/v3-dashboard/css/components.css")
        assert status == 200
        assert "text/css" in headers.get("Content-Type", "")
        assert b"filter-dropdown-menu" in body
    finally:
        server.stop()


def test_legacy_dashboard_is_deprecated_but_available_for_rollback():
    state = DashboardState()
    server = DashboardServer(state, host="127.0.0.1", port=0)
    assert server.start() is True
    try:
        status, headers, body = _get(server.port, "/legacy-dashboard/")
        html = body.decode("utf-8")
        assert status == 200
        assert headers.get("X-Dashboard-Deprecated") == "legacy"
        assert 'id="page-live"' in html

        status, headers, body = _get(server.port, "/parent-dashboard.html")
        assert status == 200
        assert headers.get("X-Dashboard-Deprecated") == "legacy"
        assert 'id="page-live"' in body.decode("utf-8")
    finally:
        server.stop()


def test_v3_dashboard_compat_status_and_history_gallery_shape():
    state = DashboardState()
    state.set_camera_snapshot_provider(lambda: b"\xff\xd8\xff\xd9")
    server = DashboardServer(state, host="127.0.0.1", port=0)
    assert server.start() is True
    try:
        status = _get_json(server.port, "/api/status")
        assert status["ok"] is True
        assert status["camera"]["frameUrl"].startswith("/api/camera/snapshot")
        assert "temperatureC" in status["environment"]
        assert "state" in status["child"]
        assert "children" in status["sleep"]

        gallery = _get_json(
            server.port,
            "/api/history/gallery?category=safety,reading&from=2026-06-29&to=2026-06-30",
        )
        assert gallery["ok"] is True
        assert gallery["selectedCategories"] == ["safety", "reading"]
        assert gallery["dateRows"]
        assert {"safety", "reading"} >= {
            item["category"]
            for row in gallery["dateRows"]
            for item in row["items"]
        }
    finally:
        server.stop()


def test_v3_dashboard_drive_controls_repeat_while_pressed():
    app_js = (_DASHBOARD_DIR / "v3_static" / "js" / "app.js").read_text(encoding="utf-8")
    render_js = (_DASHBOARD_DIR / "v3_static" / "js" / "render.js").read_text(encoding="utf-8")
    api_js = (_DASHBOARD_DIR / "v3_static" / "js" / "api.js").read_text(encoding="utf-8")

    assert "let moveRepeatTimer" in app_js
    assert "setInterval" in app_js
    assert "MOVE_REPEAT_MS" in app_js
    assert "let moveInFlight" in app_js
    assert "let pendingMoveDirection" in app_js
    assert "queueMoveCommand" in app_js
    assert "activeMoveDirection !== \"stop\"" in app_js
    assert '"pointercancel"' in app_js
    assert '"lostpointercapture"' in app_js
    assert '"blur"' in app_js
    assert "let cameraFrameInFlight" in app_js
    assert "refreshCameraFrame" in app_js
    assert "if (cameraFrameInFlight || activeMoveDirection !== \"stop\") return false;" in app_js
    assert "snapshot: () => getBlob" in api_js
    assert "renderCameraFrameBlob" in render_js


def test_v3_dashboard_history_uses_full_page_responsive_grid():
    css = (_DASHBOARD_DIR / "v3_static" / "css" / "components.css").read_text(encoding="utf-8")

    assert "repeat(auto-fill, minmax" in css
    assert ".history-card" in css
    assert "min-height: 60vh" in css
    assert ".gallery-modal" in css
    assert "width: min(1440px, 98vw)" in css


def test_v3_dashboard_filter_dropdown_is_vertical_not_two_by_two():
    css = (_DASHBOARD_DIR / "v3_static" / "css" / "components.css").read_text(encoding="utf-8")
    menu_start = css.index(".filter-dropdown-menu")
    menu_end = css.index(".filter-dropdown.open .filter-dropdown-menu")
    menu_block = css[menu_start:menu_end]

    assert "grid-template-columns: 1fr" in menu_block
    assert "repeat(2" not in menu_block
    assert "max-height" in menu_block


if __name__ == "__main__":
    test_v3_dashboard_static_entry_and_assets_are_served()
    test_legacy_dashboard_is_deprecated_but_available_for_rollback()
    test_v3_dashboard_compat_status_and_history_gallery_shape()
    test_v3_dashboard_drive_controls_repeat_while_pressed()
    test_v3_dashboard_history_uses_full_page_responsive_grid()
    test_v3_dashboard_filter_dropdown_is_vertical_not_two_by_two()
    print("test_server_v3_dashboard PASS")
