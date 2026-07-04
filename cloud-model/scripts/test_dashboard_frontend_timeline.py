"""Static checks for parent dashboard timeline rendering.

Run from repo root:
    python3 -m scripts.test_dashboard_frontend_timeline
"""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "dashboard" / "parent-dashboard.html"


def _html() -> str:
    return HTML.read_text(encoding="utf-8")


def test_frontend_declares_unified_timeline_endpoint():
    html = _html()
    assert "dashboardTimeline:" in html
    assert "/api/dashboard/timeline" in html


def test_activity_uses_timeline_renderer_and_scrolls_bottom():
    html = _html()
    assert "function renderTimeline" in html
    assert "function scrollToBottom" in html
    assert "renderTimeline(d)" in html
    assert "scrollToBottom(activityLog)" in html


def test_timeline_has_type_specific_styles():
    html = _html()
    required = [
        ".timeline-item.kind-system",
        ".timeline-item.kind-parent_action",
        ".timeline-item.kind-sleep",
        ".timeline-item.kind-safety",
        ".timeline-item.kind-reading",
        ".timeline-item.kind-conversation",
    ]
    for token in required:
        assert token in html, f"missing timeline CSS selector: {token}"


def test_history_preview_can_overlay_book_corners():
    html = _html()
    assert "id=\"history-corner-overlay\"" in html
    assert "function renderHistoryCornerOverlay" in html
    assert "book_pages" in html
    assert "history-corner-polygon" in html


def test_history_page_no_longer_labels_real_snapshot_feature_as_reserved():
    html = _html()
    assert "历史画面（预留）" not in html
    assert "历史画面" in html
    assert "安全事件截图" in html
    assert "读书截图" in html


def test_reading_records_render_compact_scrollable_rows():
    html = _html()
    assert "reading-record-list" in html
    assert "reading-record-row" in html
    assert "function sortReadingRecords" in html
    update_gallery = html.split("async function updateGallery()", 1)[1].split("async function updateActivity()", 1)[0]
    assert "book-page" not in update_gallery
    assert "scrollToBottom(gallery)" in update_gallery


def test_history_snapshots_are_grouped_by_category_rows():
    html = _html()
    assert "history-category-row" in html
    assert "function historyCategory" in html
    assert "function renderHistoryCategoryRows" in html
    assert "安全事件" in html
    assert "读书截图" in html
    assert "其他画面" in html


def test_environment_not_ready_does_not_render_zero_values():
    html = _html()
    assert "d.errors.includes('not_ready')" in html
    assert "setTimeout(updateEnv, 800)" in html


if __name__ == "__main__":
    test_frontend_declares_unified_timeline_endpoint()
    print("test_frontend_declares_unified_timeline_endpoint PASS")
    test_activity_uses_timeline_renderer_and_scrolls_bottom()
    print("test_activity_uses_timeline_renderer_and_scrolls_bottom PASS")
    test_timeline_has_type_specific_styles()
    print("test_timeline_has_type_specific_styles PASS")
    test_history_preview_can_overlay_book_corners()
    print("test_history_preview_can_overlay_book_corners PASS")
    test_history_page_no_longer_labels_real_snapshot_feature_as_reserved()
    print("test_history_page_no_longer_labels_real_snapshot_feature_as_reserved PASS")
    test_reading_records_render_compact_scrollable_rows()
    print("test_reading_records_render_compact_scrollable_rows PASS")
    test_history_snapshots_are_grouped_by_category_rows()
    print("test_history_snapshots_are_grouped_by_category_rows PASS")
    test_environment_not_ready_does_not_render_zero_values()
    print("test_environment_not_ready_does_not_render_zero_values PASS")
    print("ALL PASS")
