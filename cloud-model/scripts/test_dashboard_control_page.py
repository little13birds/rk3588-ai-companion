"""Static checks for dashboard control/sleep pages.

Run from repo root:
    python3 -m scripts.test_dashboard_control_page
"""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "dashboard" / "parent-dashboard.html"
CLIENT = ROOT / "dashboard" / "client_state.js"


def _html() -> str:
    return HTML.read_text(encoding="utf-8")


def test_control_page_is_separate_from_homepage():
    html = _html()
    live = html.split('<div class="page active" id="page-live">', 1)[1].split('<!-- Page 2:', 1)[0]
    assert 'id="page-control"' in html
    assert 'id="tab-control"' in html
    assert 'data-direction="forward"' not in live
    assert 'function updateControlStatus' in html
    assert 'systemFeatures:' in html


def test_sleep_child_controls_are_present():
    html = _html()
    assert 'id="sleep-children-input"' in html
    assert 'id="sleep-grace-input"' in html
    assert 'children:' in html
    assert 'grace_minutes:' in html


def test_client_refresh_policy_has_control_interval():
    js = CLIENT.read_text(encoding="utf-8")
    assert "controlStatusMs" in js


def test_camera_source_labels_are_rendered_for_all_camera_pages():
    html = _html()
    assert "cameraSource: '/api/camera/source'" in html
    assert 'id="cam-source"' in html
    assert 'id="cam-source-care"' in html
    assert 'id="cam-source-control"' in html
    assert "function refreshCameraSource" in html
    assert "refreshCameraSource()" in html


def test_movement_buttons_keep_command_alive_while_pressed():
    html = _html()
    assert "MOVE_COMMAND_INTERVAL_MS" in html
    assert "moveRepeatTimer" in html
    assert "startMoveRepeat(direction)" in html
    assert "sendMove(direction, {force: true})" in html
    assert "clearMoveRepeat()" in html
    assert "let moveInFlight" in html
    assert "let pendingMoveDirection" in html
    assert "queueMoveCommand(direction)" in html
    assert "cameraInflight[slotName] || activeMoveDirection !== 'stop'" in html


def test_sleep_aid_actions_update_ui_from_api_response():
    html = _html()
    assert "function applySleepStatusResponse" in html
    assert "const response = await post(ENDPOINTS.sleepAidStart" in html
    assert "applySleepStatusResponse(response);" in html
    assert "const response = await post(ENDPOINTS.sleepAidStop" in html
    assert "助眠计时中" in html
    assert "正在播放" not in html


def test_sleep_aid_api_status_starts_live_countdown():
    html = _html()
    update_fn = html.split("function updateSleepStateUI(d) {", 1)[1].split("async function saveSleepSettings()", 1)[0]
    assert "const remainingSeconds = sleepAidRemainingSeconds(d);" in update_fn
    assert "startAidCountdown(remainingSeconds);" in update_fn


def test_sleep_status_response_ignores_error_payload_without_sleep_state():
    html = _html()
    apply_fn = html.split("function applySleepStatusResponse(response) {", 1)[1].split("function updateSleepStateUI(d)", 1)[0]
    assert "response.ok === false" in apply_fn
    assert "!response.sleep" in apply_fn


def test_care_page_lists_recent_safety_event_images():
    html = _html()
    assert 'id="care-event-list"' in html
    assert "function updateCareEvents" in html
    assert "ENDPOINTS.cameraHistory" in html
    assert "ENDPOINTS.cameraHistoryImage" in html
    assert "care-event-row" in html
    assert "updateCareEvents()" in html


def test_sleep_presence_debug_status_is_rendered():
    html = _html()
    assert 'id="sleep-presence-debug"' in html
    assert "function renderSleepPresenceDebug" in html
    assert "presence.ttl_sec" in html
    assert "visible_children" in html
    assert "上报有效期" in html


def test_sleep_presence_status_refreshes_with_care_safety_loop():
    html = _html()
    js = CLIENT.read_text(encoding="utf-8")
    assert re.search(r"careSleepMs:\s*3000,", js)
    assert re.search(r"careSleepMs:\s*3000,", html)
    care_branch = html.split("if (name === 'care') {", 1)[1].split("if (name === 'control')", 1)[0]
    assert "addPageTimer(loadSleepSettings, CLIENT.REFRESH_POLICY.careSleepMs)" in care_branch
    assert care_branch.index("addPageTimer(loadSleepSettings, CLIENT.REFRESH_POLICY.careSleepMs)") < care_branch.index("addPageTimer(checkAlerts")


if __name__ == "__main__":
    test_control_page_is_separate_from_homepage()
    print("test_control_page_is_separate_from_homepage PASS")
    test_sleep_child_controls_are_present()
    print("test_sleep_child_controls_are_present PASS")
    test_client_refresh_policy_has_control_interval()
    print("test_client_refresh_policy_has_control_interval PASS")
    test_camera_source_labels_are_rendered_for_all_camera_pages()
    print("test_camera_source_labels_are_rendered_for_all_camera_pages PASS")
    test_movement_buttons_keep_command_alive_while_pressed()
    print("test_movement_buttons_keep_command_alive_while_pressed PASS")
    test_sleep_aid_actions_update_ui_from_api_response()
    print("test_sleep_aid_actions_update_ui_from_api_response PASS")
    test_sleep_aid_api_status_starts_live_countdown()
    print("test_sleep_aid_api_status_starts_live_countdown PASS")
    test_sleep_status_response_ignores_error_payload_without_sleep_state()
    print("test_sleep_status_response_ignores_error_payload_without_sleep_state PASS")
    test_care_page_lists_recent_safety_event_images()
    print("test_care_page_lists_recent_safety_event_images PASS")
    test_sleep_presence_debug_status_is_rendered()
    print("test_sleep_presence_debug_status_is_rendered PASS")
    test_sleep_presence_status_refreshes_with_care_safety_loop()
    print("test_sleep_presence_status_refreshes_with_care_safety_loop PASS")
    print("ALL PASS")
