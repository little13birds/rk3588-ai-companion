import json
import math

import numpy as np

from depth_camera_perception.person_detector import Detection
from depth_camera_perception.person_seek import (
    PersonSeekConfig,
    PersonSeekController,
    PersonTarget,
    SeekOutput,
)
from depth_camera_perception.person_seek_node import (
    _build_seek_status_json,
    _detection_to_target,
    _draw_seek_overlay,
    _obstacle_bypass_active,
    _obstacle_takeover_status,
    _person_seek_should_publish,
    _person_seek_should_publish_zero,
    _seek_status_payload,
    _select_target,
)


def make_target(
    *,
    bbox=(280.0, 120.0, 360.0, 420.0),
    distance_m=1.5,
    confidence=0.9,
):
    return PersonTarget(
        bbox=bbox,
        confidence=confidence,
        distance_m=distance_m,
        image_width=640,
        image_height=480,
    )


def test_search_rotates_until_target_seen_then_approaches():
    controller = PersonSeekController(PersonSeekConfig(search_angular_z=0.25))
    controller.start(now_s=0.0, yaw_rad=0.0)

    output = controller.update(target=None, now_s=0.1, yaw_rad=0.0)

    assert output.state == "SEARCH_ROTATE"
    assert output.reason == "searching"
    assert output.linear_x == 0.0
    assert output.angular_z == 0.25

    output = controller.update(target=make_target(), now_s=0.2, yaw_rad=0.02)

    assert output.state == "APPROACH"
    assert output.reason == "approaching_target"
    assert output.linear_x > 0.0
    assert output.angular_z == 0.0


def test_default_person_seek_forward_speed_is_04_mps():
    controller = PersonSeekController()
    controller.start(now_s=0.0, yaw_rad=0.0)

    output = controller.update(target=make_target(distance_m=1.5), now_s=0.1, yaw_rad=0.0)

    assert output.state == "APPROACH"
    assert output.linear_x == 0.4


def test_search_fails_after_one_rotation_without_target():
    controller = PersonSeekController(
        PersonSeekConfig(search_angular_z=0.25, search_max_yaw_rad=math.pi)
    )
    controller.start(now_s=0.0, yaw_rad=0.0)

    controller.update(target=None, now_s=0.1, yaw_rad=0.0)
    output = controller.update(target=None, now_s=1.0, yaw_rad=math.pi)

    assert output.state == "SEARCH_FAILED"
    assert output.reason == "search_complete_no_target"
    assert output.linear_x == 0.0
    assert output.angular_z == 0.0
    assert output.scan_yaw_rad >= math.pi


def test_search_fails_after_timeout_without_odom_yaw():
    controller = PersonSeekController(PersonSeekConfig(search_timeout_s=2.0))
    controller.start(now_s=10.0, yaw_rad=None)

    output = controller.update(target=None, now_s=12.1, yaw_rad=None)

    assert output.state == "SEARCH_FAILED"
    assert output.reason == "search_timeout_no_target"
    assert output.linear_x == 0.0
    assert output.angular_z == 0.0


def test_approach_stops_at_target_distance():
    controller = PersonSeekController(
        PersonSeekConfig(stop_distance_m=0.8, stop_tolerance_m=0.05)
    )
    controller.start(now_s=0.0, yaw_rad=0.0)
    controller.update(target=make_target(distance_m=1.2), now_s=0.1, yaw_rad=0.0)

    output = controller.update(target=make_target(distance_m=0.84), now_s=0.2, yaw_rad=0.0)

    assert output.state == "ARRIVED"
    assert output.reason == "arrived"
    assert output.linear_x == 0.0
    assert output.angular_z == 0.0


def test_approach_turns_toward_image_center_with_correct_sign():
    controller = PersonSeekController(
        PersonSeekConfig(approach_angular_gain=1.0, approach_max_angular_z=0.25)
    )
    controller.start(now_s=0.0, yaw_rad=0.0)

    right_target = make_target(bbox=(500.0, 120.0, 620.0, 420.0), distance_m=1.5)
    output = controller.update(target=right_target, now_s=0.1, yaw_rad=0.0)

    assert output.state == "APPROACH"
    assert output.target_center_error > 0.0
    assert output.angular_z < 0.0

    left_target = make_target(bbox=(20.0, 120.0, 140.0, 420.0), distance_m=1.5)
    output = controller.update(target=left_target, now_s=0.2, yaw_rad=0.0)

    assert output.target_center_error < 0.0
    assert output.angular_z > 0.0


def test_target_loss_during_approach_waits_for_timeout_before_searching_again():
    controller = PersonSeekController(
        PersonSeekConfig(target_lost_timeout_s=0.5, search_angular_z=0.25)
    )
    controller.start(now_s=0.0, yaw_rad=0.0)
    controller.update(target=make_target(distance_m=1.5), now_s=0.1, yaw_rad=0.0)

    output = controller.update(target=None, now_s=0.2, yaw_rad=0.0)

    assert output.state == "APPROACH"
    assert output.reason == "target_temporarily_lost"
    assert output.linear_x == 0.0
    assert output.angular_z == 0.0

    output = controller.update(target=None, now_s=0.7, yaw_rad=0.0)

    assert output.state == "SEARCH_ROTATE"
    assert output.reason == "searching_after_target_lost"
    assert output.linear_x == 0.0
    assert output.angular_z == 0.25


def test_target_loss_search_reacquires_when_valid_target_is_visible_again():
    controller = PersonSeekController(PersonSeekConfig(target_lost_timeout_s=0.1))
    controller.start(now_s=0.0, yaw_rad=0.0)
    controller.update(target=make_target(distance_m=1.5), now_s=0.1, yaw_rad=0.0)
    output = controller.update(target=None, now_s=0.3, yaw_rad=0.0)

    assert output.state == "SEARCH_ROTATE"

    output = controller.update(target=make_target(distance_m=1.5), now_s=0.4, yaw_rad=0.0)

    assert output.state == "APPROACH"
    assert output.reason == "approaching_target"
    assert output.linear_x > 0.0


def test_obstacle_bypass_active_detects_non_cruise_phase():
    assert _obstacle_bypass_active({"avoidance_phase": "turn_away"}) is True
    assert _obstacle_bypass_active({"avoidance_phase": "avoid_forward"}) is True
    assert _obstacle_bypass_active({"avoidance_phase": "front_turn_clear_hold"}) is True
    assert _obstacle_bypass_active({"avoidance_phase": "avoid_forward_turn_clear_hold"}) is True
    assert _obstacle_bypass_active({"avoidance_phase": "exit_forward_hold"}) is True
    assert _obstacle_bypass_active({"avoidance_phase": "avoid_forward_exit_hold"}) is True
    assert _obstacle_bypass_active({
        "avoidance_phase": "avoid_forward",
        "reason": "avoid_forward_return_heading",
    }) is True
    assert _obstacle_bypass_active({
        "avoidance_phase": "avoid_forward",
        "reason": "avoid_forward_return_heading",
    }, target_available=True) is False
    assert _obstacle_bypass_active({
        "avoidance_phase": "exit_forward_hold",
        "reason": "avoid_forward_exit_hold",
    }) is True
    assert _obstacle_bypass_active({
        "avoidance_phase": "exit_forward_hold",
        "reason": "avoid_forward_exit_hold",
    }, target_available=True) is False
    assert _obstacle_bypass_active({
        "avoidance_phase": "avoid_forward",
        "reason": "avoid_forward_min_forward",
    }, target_available=True) is True
    assert _obstacle_bypass_active({
        "avoidance_phase": "avoid_forward",
        "reason": "avoid_forward_min_forward",
    }) is True
    assert _obstacle_bypass_active({"avoidance_phase": "return_heading"}) is True
    assert _obstacle_bypass_active({"avoidance_phase": "pose_stalled_stop"}) is True
    assert _obstacle_bypass_active({"avoidance_phase": "cruise"}) is False
    assert _obstacle_bypass_active({"avoidance_phase": None}) is False
    assert _obstacle_bypass_active({}) is False


def test_obstacle_takeover_status_suppresses_person_seek_command_publish():
    previous = PersonSeekController().update(target=None, now_s=0.0, yaw_rad=None)

    output = _obstacle_takeover_status(previous)

    assert output.state == "OBSTACLE_TAKEOVER"
    assert output.reason == "obstacle_guard_takeover"
    assert output.linear_x == 0.0
    assert output.angular_z == 0.0
    assert _person_seek_should_publish(output) is False
    controller = PersonSeekController()
    controller.start(now_s=0.0, yaw_rad=0.0)
    assert _person_seek_should_publish(
        controller.update(target=make_target(distance_m=1.5), now_s=0.1)
    ) is True


def test_obstacle_takeover_suppresses_zero_publish_even_when_camera_is_stale():
    previous = PersonSeekController().update(target=None, now_s=0.0, yaw_rad=None)

    output = _obstacle_takeover_status(previous)

    assert output.state == "OBSTACLE_TAKEOVER"
    assert _person_seek_should_publish(output) is False
    assert _person_seek_should_publish_zero(output) is False
    assert _person_seek_should_publish_zero(
        SeekOutput(state="SEARCH_FAILED", reason="search_timeout_no_target")
    ) is True


def test_person_seek_resumes_search_after_obstacle_if_target_was_lost():
    controller = PersonSeekController(PersonSeekConfig(target_lost_timeout_s=0.5))
    controller.start(now_s=0.0, yaw_rad=0.0)
    approach = controller.update(target=make_target(distance_m=1.5), now_s=0.1, yaw_rad=0.0)
    takeover = _obstacle_takeover_status(approach)

    assert takeover.state == "OBSTACLE_TAKEOVER"

    output = controller.update(target=None, now_s=1.0, yaw_rad=0.0)

    assert output.state == "SEARCH_ROTATE"
    assert output.reason == "searching_after_target_lost"


def test_person_seek_resumes_approach_after_obstacle_if_target_is_visible():
    controller = PersonSeekController(PersonSeekConfig(target_lost_timeout_s=0.5))
    controller.start(now_s=0.0, yaw_rad=0.0)
    approach = controller.update(target=make_target(distance_m=1.5), now_s=0.1, yaw_rad=0.0)
    takeover = _obstacle_takeover_status(approach)

    assert takeover.state == "OBSTACLE_TAKEOVER"

    output = controller.update(target=make_target(distance_m=1.4), now_s=1.0, yaw_rad=0.0)

    assert output.state == "APPROACH"
    assert output.reason == "approaching_target"
    assert output.linear_x > 0.0


def test_detection_to_target_requires_valid_distance():
    detection = Detection(
        bbox=(100.0, 100.0, 200.0, 400.0),
        confidence=0.8,
        class_id=0,
        label="person",
        distance_m=None,
    )

    assert _detection_to_target(detection, image_width=640, image_height=480) is None

    target = _detection_to_target(
        detection.with_distance(1.4),
        image_width=640,
        image_height=480,
    )

    assert target is not None
    assert target.distance_m == 1.4
    assert target.confidence == 0.8


def test_select_target_uses_nearest_valid_person():
    detections = [
        Detection((100, 100, 200, 400), 0.8, 0, "person").with_distance(2.0),
        Detection((300, 100, 420, 420), 0.7, 0, "person").with_distance(None),
        Detection((420, 100, 560, 420), 0.9, 0, "person").with_distance(1.2),
    ]

    target = _select_target(detections, image_width=640, image_height=480)

    assert target is not None
    assert target.distance_m == 1.2
    assert target.bbox == (420, 100, 560, 420)


def test_status_json_reports_output_and_target():
    controller = PersonSeekController()
    controller.start(now_s=0.0, yaw_rad=0.0)
    output = controller.update(target=make_target(distance_m=1.5), now_s=0.1, yaw_rad=0.0)

    payload = json.loads(_build_seek_status_json(output, target_count=2))

    assert payload["state"] == "APPROACH"
    assert payload["reason"] == "approaching_target"
    assert payload["target_count"] == 2
    assert payload["target_distance_m"] == 1.5
    assert payload["output_linear_x"] == output.linear_x
    assert payload["output_angular_z"] == output.angular_z


def test_status_payload_includes_web_runtime_fields():
    output = PersonSeekController().update(target=None, now_s=0.0, yaw_rad=None)

    payload = _seek_status_payload(
        output,
        target_count=0,
        image_width=640,
        image_height=480,
        web_fps=4.5,
        latest_frame_age_s=0.12,
    )

    assert payload["image"]["width"] == 640
    assert payload["image"]["height"] == 480
    assert payload["fps"]["web"] == 4.5
    assert payload["latest_frame_age_s"] == 0.12
    assert payload["motion"]["linear_x"] == output.linear_x


def test_seek_status_payload_includes_identity_fields():
    payload = _seek_status_payload(
        SeekOutput(state="APPROACH", reason="approaching_target", linear_x=0.2),
        target_count=1,
        identity_status={
            "mode": "identity",
            "target_name": "tao",
            "target_person_id": "tao",
            "target_track_id": 9,
            "identity_state": "TRACKING_ID",
            "identity_reason": "target_identity_bound",
            "identity_score": 0.81,
            "temporary_lost_due_to_obstacle": False,
        },
    )

    assert payload["identity"]["mode"] == "identity"
    assert payload["identity"]["target_name"] == "tao"
    assert payload["identity"]["target_track_id"] == 9


def test_draw_seek_overlay_marks_selected_target_and_status_text():
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    detections = [
        Detection((10, 20, 50, 100), 0.7, 0, "person").with_distance(2.0),
        Detection((90, 20, 140, 100), 0.9, 0, "person").with_distance(1.2),
    ]
    selected = _detection_to_target(detections[1], image_width=160, image_height=120)
    output = PersonSeekController().update(target=None, now_s=0.0, yaw_rad=None)

    annotated = _draw_seek_overlay(frame, detections, selected, output)

    assert annotated.shape == frame.shape
    assert int(annotated.sum()) > 0
    assert annotated[20, 90].tolist() != [0, 0, 0]
