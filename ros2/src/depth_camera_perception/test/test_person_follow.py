import math

from depth_camera_perception.person_follow import (
    FollowOutput,
    PersonFollowConfig,
    PersonFollowController,
)
from depth_camera_perception.person_follow_node import (
    _follow_obstacle_bypass_active,
    _follow_obstacle_takeover_status,
    _person_follow_should_publish,
    _person_follow_should_publish_zero,
)
from depth_camera_perception.person_seek import PersonTarget


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


def test_follow_approaches_far_target_at_default_04_mps():
    controller = PersonFollowController()
    controller.start(now_s=0.0, yaw_rad=0.0)

    output = controller.update(target=make_target(distance_m=1.8), now_s=0.1, yaw_rad=0.0)

    assert output.state == "FOLLOW"
    assert output.reason == "following_target"
    assert output.linear_x == 0.4
    assert output.angular_z == 0.0
    assert output.target_distance_m == 1.8


def test_follow_holds_position_near_12m_without_arriving_terminal_state():
    controller = PersonFollowController(
        PersonFollowConfig(follow_distance_m=1.2, distance_tolerance_m=0.08)
    )
    controller.start(now_s=0.0, yaw_rad=0.0)

    output = controller.update(target=make_target(distance_m=1.23), now_s=0.1, yaw_rad=0.0)

    assert output.state == "FOLLOW"
    assert output.reason == "holding_follow_distance"
    assert output.linear_x == 0.0
    assert output.angular_z == 0.0


def test_follow_recenters_small_in_frame_drift_at_follow_distance_by_default():
    controller = PersonFollowController()
    controller.start(now_s=0.0, yaw_rad=0.0)

    output = controller.update(
        target=make_target(bbox=(295.0, 120.0, 375.0, 420.0), distance_m=1.22),
        now_s=0.1,
        yaw_rad=0.0,
    )

    assert output.state == "FOLLOW"
    assert output.reason == "holding_follow_distance"
    assert output.linear_x == 0.0
    assert output.target_center_error > 0.0
    assert output.angular_z < 0.0


def test_follow_does_not_back_up_when_target_is_too_close():
    controller = PersonFollowController(PersonFollowConfig(follow_distance_m=1.2))
    controller.start(now_s=0.0, yaw_rad=0.0)

    output = controller.update(target=make_target(distance_m=0.9), now_s=0.1, yaw_rad=0.0)

    assert output.state == "FOLLOW"
    assert output.reason == "target_too_close"
    assert output.linear_x == 0.0


def test_follow_turns_toward_image_center_with_correct_sign():
    controller = PersonFollowController(
        PersonFollowConfig(follow_angular_gain=1.0, follow_max_angular_z=0.25)
    )
    controller.start(now_s=0.0, yaw_rad=0.0)

    right_target = make_target(bbox=(500.0, 120.0, 620.0, 420.0), distance_m=1.5)
    output = controller.update(target=right_target, now_s=0.1, yaw_rad=0.0)

    assert output.target_center_error > 0.0
    assert output.angular_z < 0.0

    left_target = make_target(bbox=(20.0, 120.0, 140.0, 420.0), distance_m=1.5)
    output = controller.update(target=left_target, now_s=0.2, yaw_rad=0.0)

    assert output.target_center_error < 0.0
    assert output.angular_z > 0.0


def test_target_loss_during_follow_restarts_search_after_timeout():
    controller = PersonFollowController(
        PersonFollowConfig(target_lost_timeout_s=0.5, search_angular_z=0.25)
    )
    controller.start(now_s=0.0, yaw_rad=0.0)
    controller.update(target=make_target(distance_m=1.8), now_s=0.1, yaw_rad=0.0)

    output = controller.update(target=None, now_s=0.2, yaw_rad=0.0)

    assert output.state == "FOLLOW"
    assert output.reason == "target_temporarily_lost"
    assert output.linear_x == 0.0
    assert output.angular_z == 0.0

    output = controller.update(target=None, now_s=0.7, yaw_rad=0.0)

    assert output.state == "SEARCH_ROTATE"
    assert output.reason == "searching_after_target_lost"
    assert output.linear_x == 0.0
    assert output.angular_z == 0.25


def test_default_reacquire_search_speed_is_five_times_old_default():
    controller = PersonFollowController()
    controller.start(now_s=0.0, yaw_rad=0.0)

    output = controller.update(target=None, now_s=0.1, yaw_rad=0.0)

    assert output.state == "SEARCH_ROTATE"
    assert output.reason == "searching"
    assert output.angular_z == 1.25


def test_follow_resumes_after_obstacle_if_target_is_visible():
    controller = PersonFollowController(PersonFollowConfig(target_lost_timeout_s=0.5))
    controller.start(now_s=0.0, yaw_rad=0.0)
    follow = controller.update(target=make_target(distance_m=1.8), now_s=0.1, yaw_rad=0.0)
    takeover = _follow_obstacle_takeover_status(follow)

    assert takeover.state == "OBSTACLE_TAKEOVER"

    output = controller.update(target=make_target(distance_m=1.5), now_s=1.0, yaw_rad=0.0)

    assert output.state == "FOLLOW"
    assert output.reason == "following_target"
    assert output.linear_x > 0.0


def test_follow_resumes_search_after_obstacle_if_target_was_lost():
    controller = PersonFollowController(PersonFollowConfig(target_lost_timeout_s=0.5))
    controller.start(now_s=0.0, yaw_rad=0.0)
    follow = controller.update(target=make_target(distance_m=1.8), now_s=0.1, yaw_rad=0.0)
    takeover = _follow_obstacle_takeover_status(follow)

    assert takeover.state == "OBSTACLE_TAKEOVER"

    output = controller.update(target=None, now_s=1.0, yaw_rad=0.0)

    assert output.state == "SEARCH_ROTATE"
    assert output.reason == "searching_after_target_lost"


def test_follow_obstacle_bypass_active_detects_project2_hold_phases():
    assert _follow_obstacle_bypass_active({"avoidance_phase": "turn_away"}) is True
    assert _follow_obstacle_bypass_active({"avoidance_phase": "avoid_forward"}) is True
    assert _follow_obstacle_bypass_active({"avoidance_phase": "front_turn_clear_hold"}) is True
    assert _follow_obstacle_bypass_active({"avoidance_phase": "avoid_forward_turn_clear_hold"}) is True
    assert _follow_obstacle_bypass_active({"avoidance_phase": "exit_forward_hold"}) is True
    assert _follow_obstacle_bypass_active({"avoidance_phase": "avoid_forward_exit_hold"}) is True
    assert _follow_obstacle_bypass_active({
        "avoidance_phase": "avoid_forward",
        "reason": "avoid_forward_return_heading",
    }) is True
    assert _follow_obstacle_bypass_active({
        "avoidance_phase": "avoid_forward",
        "reason": "avoid_forward_return_heading",
    }, target_available=True) is False
    assert _follow_obstacle_bypass_active({
        "avoidance_phase": "exit_forward_hold",
        "reason": "avoid_forward_exit_hold",
    }) is True
    assert _follow_obstacle_bypass_active({
        "avoidance_phase": "exit_forward_hold",
        "reason": "avoid_forward_exit_hold",
    }, target_available=True) is False
    assert _follow_obstacle_bypass_active({
        "avoidance_phase": "avoid_forward",
        "reason": "avoid_forward_min_forward",
    }, target_available=True) is True
    assert _follow_obstacle_bypass_active({
        "avoidance_phase": "avoid_forward",
        "reason": "avoid_forward_min_forward",
    }) is True
    assert _follow_obstacle_bypass_active({"avoidance_phase": "return_heading"}) is True
    assert _follow_obstacle_bypass_active({"avoidance_phase": "pose_stalled_stop"}) is True
    assert _follow_obstacle_bypass_active({"avoidance_phase": "cruise"}) is False
    assert _follow_obstacle_bypass_active({"avoidance_phase": None}) is False
    assert _follow_obstacle_bypass_active({}) is False


def test_obstacle_takeover_suppresses_follow_velocity_and_zero_publish():
    previous = FollowOutput(state="FOLLOW", reason="following_target", linear_x=0.4, angular_z=0.1)

    output = _follow_obstacle_takeover_status(previous)

    assert output.state == "OBSTACLE_TAKEOVER"
    assert output.reason == "obstacle_guard_takeover"
    assert output.linear_x == 0.0
    assert output.angular_z == 0.0
    assert _person_follow_should_publish(output) is False
    assert _person_follow_should_publish_zero(output) is False
    assert _person_follow_should_publish_zero(
        FollowOutput(state="SEARCH_FAILED", reason="search_timeout_no_target")
    ) is True


def test_search_fails_after_one_rotation_without_target():
    controller = PersonFollowController(
        PersonFollowConfig(search_angular_z=0.25, search_max_yaw_rad=math.pi)
    )
    controller.start(now_s=0.0, yaw_rad=0.0)

    controller.update(target=None, now_s=0.1, yaw_rad=0.0)
    output = controller.update(target=None, now_s=1.0, yaw_rad=math.pi)

    assert output.state == "SEARCH_FAILED"
    assert output.reason == "search_complete_no_target"
    assert output.linear_x == 0.0
    assert output.angular_z == 0.0


from depth_camera_perception.person_follow_node import _follow_status_payload


def test_follow_status_payload_includes_identity_fields():
    payload = _follow_status_payload(
        FollowOutput(state="FOLLOW", reason="following_target", linear_x=0.1),
        target_count=1,
        follow_distance_m=1.2,
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
    assert payload["identity"]["identity_score"] == 0.81
