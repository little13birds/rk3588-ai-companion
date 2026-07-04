"""Tests for arm_agent debug-page metadata helpers."""

from face_track.arm_agent_core import (
    InferenceStats,
    build_joint_debug,
    build_book_debug,
    scaled_debug_size,
)


def test_build_book_debug_extracts_corners_center_and_angle():
    result = {
        "found": True,
        "num_pages": 2,
        "center": [640.0, 360.0],
        "area_ratio": 0.32,
        "corners": {
            "tl": [100.0, 100.0, 0.95],
            "tr": [300.0, 100.0, 0.94],
            "br": [310.0, 260.0, 0.93],
            "bl": [90.0, 260.0, 0.92],
        },
        "pages": [
            {"conf": 0.9, "center": [200.0, 180.0], "bbox": [80.0, 90.0, 110.0, 150.0]},
        ],
    }

    debug = build_book_debug(result, width=1280, height=720)

    assert debug["found"] is True
    assert debug["frame"] == {"width": 1280, "height": 720}
    assert debug["center"] == {"x": 640.0, "y": 360.0, "nx": 0.5, "ny": 0.5}
    assert debug["area_ratio"] == 0.32
    assert debug["num_pages"] == 2
    assert debug["angle_deg"] == 0.0
    assert debug["corners"][0] == {"name": "tl", "x": 100.0, "y": 100.0, "conf": 0.95}
    assert debug["pages"][0]["bbox"] == [80.0, 90.0, 110.0, 150.0]
    print("test_build_book_debug_extracts_corners_center_and_angle PASS")


def test_build_book_debug_computes_tilt_from_top_edge():
    result = {
        "found": True,
        "center": [50.0, 50.0],
        "corners": {
            "tl": [0.0, 0.0, 1.0],
            "tr": [100.0, 100.0, 1.0],
            "br": [100.0, 200.0, 1.0],
            "bl": [0.0, 100.0, 1.0],
        },
    }

    debug = build_book_debug(result, width=200, height=200)

    assert debug["angle_deg"] == 45.0
    print("test_build_book_debug_computes_tilt_from_top_edge PASS")


def test_build_book_debug_handles_missing_book():
    debug = build_book_debug({"found": False, "num_pages": 0}, width=1280, height=720)

    assert debug["found"] is False
    assert debug["center"] is None
    assert debug["corners"] == []
    assert debug["pages"] == []
    assert debug["angle_deg"] is None
    print("test_build_book_debug_handles_missing_book PASS")


def test_scaled_debug_size_preserves_aspect_ratio():
    assert scaled_debug_size(1280, 720, 480) == (480, 270)
    assert scaled_debug_size(320, 240, 480) == (320, 240)
    assert scaled_debug_size(1280, 720, 0) == (1280, 720)
    print("test_scaled_debug_size_preserves_aspect_ratio PASS")


def test_inference_stats_reports_fps_and_latency():
    now = [10.0]
    stats = InferenceStats(time_fn=lambda: now[0], report_interval=1.0)

    stats.record(0.020)
    now[0] = 10.5
    stats.record(0.040)
    assert stats.snapshot()["inference_fps"] == 0.0

    now[0] = 11.0
    stats.record(0.030)
    snap = stats.snapshot()

    assert snap["inference_fps"] == 3.0
    assert snap["last_infer_ms"] == 30.0
    assert snap["avg_infer_ms"] == 27.2
    assert snap["inference_count"] == 3
    print("test_inference_stats_reports_fps_and_latency PASS")


def test_build_joint_debug_names_positions_for_web_panel():
    debug = build_joint_debug(
        [
            "base_link_to_link1",
            "link1_to_link2",
            "link2_to_link3",
            "link3_to_gripper_link",
        ],
        [0.123456, -0.2, 2.618, 0.0],
    )

    assert debug["count"] == 4
    assert debug["ordered"][0] == {
        "name": "base_link_to_link1",
        "position": 0.1235,
    }
    assert debug["positions"]["link2_to_link3"] == 2.618
    print("test_build_joint_debug_names_positions_for_web_panel PASS")


def test_debug_page_exposes_standalone_arm_controls():
    from face_track.arm_agent import _debug_page_html

    html = _debug_page_html()

    assert "prepareBtn" in html
    assert "startBtn" in html
    assert "stopBtn" in html
    assert "homeBtn" in html
    assert "/reading/prepare?timeout=12" in html
    assert "/reading/start" in html
    assert "/reading/stop" in html
    assert "/reading/stop?return_home=1" in html
    assert "Joint Positions" in html
    assert "Alignment Error" in html
    print("test_debug_page_exposes_standalone_arm_controls PASS")


if __name__ == "__main__":
    test_build_book_debug_extracts_corners_center_and_angle()
    test_build_book_debug_computes_tilt_from_top_edge()
    test_build_book_debug_handles_missing_book()
    test_scaled_debug_size_preserves_aspect_ratio()
    test_inference_stats_reports_fps_and_latency()
    test_build_joint_debug_names_positions_for_web_panel()
    test_debug_page_exposes_standalone_arm_controls()
    print("ALL PASS")
