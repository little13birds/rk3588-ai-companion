"""Static checks for dashboard people management UI.

Run from repo root:
    python3 -m scripts.test_dashboard_people_page
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "dashboard" / "parent-dashboard.html"


def _html() -> str:
    return HTML.read_text(encoding="utf-8")


def test_people_page_and_endpoints_exist():
    html = _html()
    assert 'id="page-people"' in html
    assert 'id="tab-people"' in html
    assert "people: '/api/people'" in html
    assert "peopleUploadCandidates: '/api/people/candidates/upload'" in html
    assert "peopleCaptureCandidates: '/api/people/candidates/capture'" in html
    assert "peopleEnroll: '/api/people/enroll'" in html
    assert "peopleDelete: '/api/people/delete'" in html


def test_people_enrollment_ui_hides_embeddings_from_browser():
    html = _html()
    assert 'id="people-list"' in html
    assert 'id="people-candidates"' in html
    assert 'id="people-upload-input"' in html
    assert "function loadPeople" in html
    assert "function renderPeopleCandidates" in html
    assert "candidate_id" in html
    assert "embedding" not in html.split("function renderPeopleCandidates", 1)[1].split("async function enrollPersonCandidate", 1)[0]


def test_sleep_settings_use_people_checkboxes():
    html = _html()
    assert 'id="sleep-children-list"' in html
    assert "function renderSleepChildrenCheckboxes" in html
    assert "function collectSelectedSleepChildren" in html
    assert "children: collectSelectedSleepChildren()" in html


def test_find_child_modal_has_remembered_target_and_stop():
    html = _html()
    assert 'id="find-child-modal"' in html
    assert 'id="find-child-options"' in html
    assert 'id="find-child-status"' in html
    assert "dashboard:lastFindTarget" in html
    assert "function openFindChildModal" in html
    assert "function startFindChildWithSelection" in html
    assert "function stopPersonTask" in html
    assert "personTaskSeek: '/api/person-task/seek'" in html
    assert "personTaskStop: '/api/person-task/stop'" in html
    assert "personTaskStatus: '/api/person-task/status'" in html


def test_find_child_modal_loads_people_before_rendering_and_prefers_children():
    html = _html()
    assert "async function openFindChildModal" in html
    assert "await loadPeople()" in html
    assert "await loadSleepChildrenForFind()" in html
    assert "function findTargetPeople" in html
    assert "sleepChildrenCache" in html
    assert "findTargetPeople()" in html


def test_find_child_start_has_duplicate_click_guard():
    html = _html()
    assert "let personTaskStarting = false" in html
    assert "if (personTaskStarting) return" in html
    assert "personTaskStarting = true" in html
    assert "personTaskStarting = false" in html


if __name__ == "__main__":
    test_people_page_and_endpoints_exist()
    print("test_people_page_and_endpoints_exist PASS")
    test_people_enrollment_ui_hides_embeddings_from_browser()
    print("test_people_enrollment_ui_hides_embeddings_from_browser PASS")
    test_sleep_settings_use_people_checkboxes()
    print("test_sleep_settings_use_people_checkboxes PASS")
    test_find_child_modal_has_remembered_target_and_stop()
    print("test_find_child_modal_has_remembered_target_and_stop PASS")
    test_find_child_modal_loads_people_before_rendering_and_prefers_children()
    print("test_find_child_modal_loads_people_before_rendering_and_prefers_children PASS")
    test_find_child_start_has_duplicate_click_guard()
    print("test_find_child_start_has_duplicate_click_guard PASS")
    print("ALL PASS")
