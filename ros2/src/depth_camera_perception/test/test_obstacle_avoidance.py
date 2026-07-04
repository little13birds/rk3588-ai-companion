import json

import numpy as np
from geometry_msgs.msg import Twist

import depth_camera_perception.obstacle_guard_node as obstacle_guard_node
from depth_camera_perception.obstacle_guard_node import (
    _make_takeover_cmd,
    _select_guard_input_cmd,
    _status_json,
)
from depth_camera_perception.obstacle_avoidance import (
    CompleteBypassConfig,
    CompleteBypassController,
    CompleteBypassState,
    ObstacleConfig,
    ObstaclePose,
    ObstacleStatus,
    analyze_depth_zones,
    guard_velocity,
)


def make_depth(value_m: float) -> np.ndarray:
    return np.full((120, 160), value_m, dtype=np.float32)


def test_clear_depth_reports_clear_front_and_sides():
    result = analyze_depth_zones(make_depth(2.0), ObstacleConfig())
    assert result.state == 'clear'
    assert result.front_blocked is False
    assert result.left_clear is True
    assert result.right_clear is True


def test_close_front_obstacle_blocks_front_but_keeps_clear_side():
    depth = make_depth(2.0)
    depth[48:96, 64:96] = 0.35
    result = analyze_depth_zones(depth, ObstacleConfig(danger_distance_m=0.55))
    assert result.state == 'blocked'
    assert result.front_blocked is True
    assert result.front_distance_m < 0.55
    assert result.left_clear is True
    assert result.right_clear is True


def test_invalid_depth_samples_are_ignored():
    depth = make_depth(2.0)
    depth[:, :] = np.nan
    depth[50:70, 70:90] = 0.4
    result = analyze_depth_zones(depth, ObstacleConfig(danger_distance_m=0.55))
    assert result.state == 'blocked'
    assert result.front_blocked is True


def test_default_front_roi_covers_wider_center_band():
    depth = make_depth(2.0)
    depth[48:96, 40:56] = 0.35
    result = analyze_depth_zones(depth, ObstacleConfig(danger_distance_m=0.55))
    assert result.state == 'blocked'
    assert result.front_blocked is True
    assert result.front_distance_m < 0.55


def test_missing_front_depth_is_treated_as_blocked():
    depth = make_depth(2.0)
    depth[48:102, 32:128] = np.nan
    result = analyze_depth_zones(depth, ObstacleConfig())
    assert result.state == 'blocked'
    assert result.front_blocked is True
    assert result.front_distance_m is None


def test_large_invalid_front_depth_region_blocks_even_with_far_background():
    depth = make_depth(2.0)
    depth[48:102, 40:120] = np.nan
    depth[48:102, 92:120] = 2.0

    result = analyze_depth_zones(depth, ObstacleConfig(front_invalid_depth_block_fraction=0.35))

    assert result.state == 'blocked'
    assert result.front_blocked is True
    assert result.front_distance_m == 2.0
    assert result.front_invalid_fraction > 0.35


def test_missing_side_depth_is_not_clear():
    depth = make_depth(2.0)
    depth[48:102, :40] = np.nan
    depth[48:102, 120:] = np.nan
    result = analyze_depth_zones(depth, ObstacleConfig())
    assert result.left_clear is False
    assert result.right_clear is False


def test_front_and_side_invalid_depth_thresholds_are_independent():
    depth = make_depth(2.0)
    depth[48:102, 40:80] = np.nan
    depth[48:102, 0:30] = np.nan
    depth[48:102, 130:160] = np.nan

    result = analyze_depth_zones(
        depth,
        ObstacleConfig(
            front_invalid_depth_block_fraction=0.40,
            side_invalid_depth_block_fraction=0.90,
        ),
    )

    assert result.front_blocked is True
    assert result.front_distance_m == 2.0
    assert result.left_clear is True
    assert result.right_clear is True
    assert result.left_distance_m == 2.0
    assert result.right_distance_m == 2.0


def make_twist(linear_x: float, angular_z: float = 0.0) -> Twist:
    msg = Twist()
    msg.linear.x = linear_x
    msg.angular.z = angular_z
    return msg


def blocked_status(left_distance_m: float = 2.0, right_distance_m: float = 2.0):
    return analyze_depth_zones(make_depth(0.3), ObstacleConfig()).__class__(
        state='blocked',
        front_distance_m=0.3,
        left_distance_m=left_distance_m,
        right_distance_m=right_distance_m,
        front_blocked=True,
        left_clear=left_distance_m is not None and left_distance_m >= 0.8,
        right_clear=right_distance_m is not None and right_distance_m >= 0.8,
    )


def clear_status():
    return ObstacleStatus(
        state='clear',
        front_distance_m=2.0,
        left_distance_m=2.0,
        right_distance_m=2.0,
        front_blocked=False,
        left_clear=True,
        right_clear=True,
    )


def side_obstacle_status(side: str):
    left_distance = 0.55 if side == 'left' else 1.6
    right_distance = 0.55 if side == 'right' else 1.6
    return ObstacleStatus(
        state='clear',
        front_distance_m=1.6,
        left_distance_m=left_distance,
        right_distance_m=right_distance,
        front_blocked=False,
        left_clear=left_distance >= 0.8,
        right_clear=right_distance >= 0.8,
    )


def side_invalid_and_valid_obstacle_status(*, invalid_side: str):
    left_invalid = invalid_side == 'left'
    right_invalid = invalid_side == 'right'
    return ObstacleStatus(
        state='clear',
        front_distance_m=1.6,
        left_distance_m=2.0 if left_invalid else 0.4,
        right_distance_m=2.0 if right_invalid else 0.4,
        front_blocked=False,
        left_clear=False,
        right_clear=False,
        left_invalid_fraction=0.95 if left_invalid else 0.05,
        right_invalid_fraction=0.95 if right_invalid else 0.05,
    )


def make_pose(x: float = 0.0, y: float = 0.0, yaw: float = 0.0) -> ObstaclePose:
    return ObstaclePose(x=x, y=y, yaw=yaw)


def test_guard_stops_forward_motion_when_blocked_and_bypass_disabled():
    guarded = guard_velocity(make_twist(0.2), blocked_status(), allow_bypass=False)
    assert guarded.linear.x == 0.0
    assert guarded.angular.z == 0.0


def test_guard_turns_toward_clearer_side_when_bypass_enabled():
    guarded = guard_velocity(
        make_twist(0.2),
        blocked_status(left_distance_m=1.2, right_distance_m=0.4),
        allow_bypass=True,
    )
    assert guarded.linear.x == 0.0
    assert guarded.angular.z > 0.0


def test_guard_turns_toward_farther_side_even_when_neither_side_is_clear():
    guarded = guard_velocity(
        make_twist(0.2),
        blocked_status(left_distance_m=0.72, right_distance_m=0.45),
        allow_bypass=True,
    )

    assert guarded.linear.x == 0.0
    assert guarded.angular.z > 0.0


def test_guard_does_not_choose_side_with_missing_depth():
    guarded = guard_velocity(
        make_twist(0.2),
        blocked_status(left_distance_m=None, right_distance_m=1.2),
        allow_bypass=True,
    )
    assert guarded.linear.x == 0.0
    assert guarded.angular.z < 0.0


def test_guard_allows_reverse_and_in_place_rotation_when_front_blocked():
    blocked = blocked_status()
    assert guard_velocity(make_twist(-0.1), blocked, allow_bypass=False).linear.x < 0.0
    assert guard_velocity(make_twist(0.0, 0.2), blocked, allow_bypass=False).angular.z == 0.2


def test_complete_bypass_records_origin_and_tracks_obstacle_side():
    controller = CompleteBypassController(
        CompleteBypassConfig(
            normal_forward_mps=0.4,
            bypass_forward_mps=0.2,
            bypass_angular_z=0.25,
        )
    )
    desired = make_twist(0.4)

    output, reason = controller.filter_velocity(
        desired,
        blocked_status(left_distance_m=2.0, right_distance_m=1.0),
        make_pose(x=1.0, y=2.0, yaw=0.3),
    )

    assert reason == 'turn_away'
    assert controller.state.phase == 'turn_away'
    assert controller.state.origin_x == 1.0
    assert controller.state.origin_y == 2.0
    assert controller.state.origin_yaw == 0.3
    assert controller.state.direction == 1
    assert controller.state.tracked_side == 'right'
    assert output.linear.x == 0.0
    assert output.angular.z > 0.0


def test_complete_bypass_turns_toward_farther_side_even_when_neither_side_is_clear():
    controller = CompleteBypassController(CompleteBypassConfig(bypass_angular_z=0.25))
    desired = make_twist(0.4)

    output, reason = controller.filter_velocity(
        desired,
        blocked_status(left_distance_m=0.42, right_distance_m=0.68),
        make_pose(),
    )

    assert reason == 'turn_away'
    assert controller.state.direction == -1
    assert controller.state.tracked_side == 'left'
    assert output.linear.x == 0.0
    assert output.angular.z < 0.0


def test_complete_bypass_takes_over_forward_turning_command_when_front_blocked():
    controller = CompleteBypassController(CompleteBypassConfig(bypass_angular_z=0.25))
    desired = make_twist(0.4, 0.2)

    output, reason = controller.filter_velocity(
        desired,
        blocked_status(left_distance_m=1.4, right_distance_m=0.8),
        make_pose(),
    )

    assert reason == 'turn_away'
    assert controller.state.phase == 'turn_away'
    assert output.linear.x == 0.0
    assert output.angular.z > 0.0


def test_guard_uses_takeover_cmd_when_upstream_cmd_is_stale_during_bypass():
    latest = make_twist(0.4, 0.2)
    takeover = _make_takeover_cmd(latest)

    selected, using_takeover = _select_guard_input_cmd(
        latest_cmd=latest,
        cmd_age_s=1.0,
        cmd_timeout_s=0.5,
        bypass_phase='avoid_forward',
        takeover_cmd=takeover,
    )

    assert using_takeover is True
    assert selected is takeover
    assert selected.linear.x == 0.4
    assert selected.angular.z == 0.0


def test_guard_does_not_use_takeover_cmd_after_bypass_returns_to_cruise():
    takeover = _make_takeover_cmd(make_twist(0.4, 0.2))

    selected, using_takeover = _select_guard_input_cmd(
        latest_cmd=None,
        cmd_age_s=None,
        cmd_timeout_s=0.5,
        bypass_phase='cruise',
        takeover_cmd=takeover,
    )

    assert using_takeover is False
    assert selected is None


def test_complete_bypass_front_blocked_interrupts_return_to_line_with_turn_away():
    controller = CompleteBypassController(CompleteBypassConfig(bypass_angular_z=0.25))
    desired = make_twist(0.4)
    controller.state.phase = 'return_to_line'
    controller.state.origin_x = 0.0
    controller.state.origin_y = 0.0
    controller.state.origin_yaw = 0.0
    controller.state.direction = 1
    controller.state.tracked_side = 'right'

    output, reason = controller.filter_velocity(
        desired,
        blocked_status(left_distance_m=0.50, right_distance_m=1.10),
        make_pose(x=0.5, y=0.2, yaw=0.3),
    )

    assert reason == 'turn_away'
    assert controller.state.phase == 'turn_away'
    assert controller.state.direction == -1
    assert controller.state.tracked_side == 'left'
    assert output.linear.x == 0.0
    assert output.angular.z < 0.0


def test_complete_bypass_does_not_pose_stall_during_in_place_turn_away():
    controller = CompleteBypassController(
        CompleteBypassConfig(
            bypass_angular_z=0.25,
            pose_stall_timeout_s=0.5,
            pose_stall_yaw_epsilon_rad=0.02,
        )
    )
    desired = make_twist(0.4)
    blocked = blocked_status(left_distance_m=2.0, right_distance_m=1.0)

    output, reason = controller.filter_velocity(desired, blocked, make_pose(), now_s=0.0)
    assert reason == 'turn_away'
    assert output.angular.z > 0.0

    output, reason = controller.filter_velocity(desired, blocked, make_pose(), now_s=0.8)

    assert reason == 'turn_away'
    assert controller.state.phase == 'turn_away'
    assert controller.state.pose_stalled is False
    assert output.angular.z > 0.0


def test_complete_bypass_side_follows_when_front_clear_and_obstacle_on_tracked_side():
    controller = CompleteBypassController(
        CompleteBypassConfig(
            bypass_forward_mps=0.2,
            return_angular_z=0.25,
        )
    )
    desired = make_twist(0.4)

    controller.filter_velocity(
        desired,
        blocked_status(left_distance_m=2.0, right_distance_m=1.0),
        make_pose(yaw=0.0),
    )
    output, reason = controller.filter_velocity(
        desired,
        side_obstacle_status('right'),
        make_pose(x=0.0, y=0.0, yaw=0.35),
    )

    assert reason == 'avoid_forward_side_steer_left'
    assert controller.state.phase == 'avoid_forward'
    assert output.linear.x == 0.2
    assert output.angular.z > 0.0


def test_complete_bypass_minimum_forward_time_delays_return_heading():
    controller = CompleteBypassController(
        CompleteBypassConfig(
            bypass_forward_mps=0.2,
            return_angular_z=0.25,
            return_heading_tolerance_rad=0.08,
            avoid_min_forward_s=1.0,
            side_clear_hold_s=0.0,
        )
    )
    desired = make_twist(0.4)

    controller.filter_velocity(
        desired,
        blocked_status(left_distance_m=2.0, right_distance_m=1.0),
        make_pose(yaw=0.0),
        now_s=0.0,
    )
    output, reason = controller.filter_velocity(
        desired,
        clear_status(),
        make_pose(x=0.05, y=0.0, yaw=0.35),
        now_s=0.4,
    )

    assert reason == 'avoid_forward_min_forward'
    assert output.linear.x == 0.2
    assert output.angular.z == 0.0

    output, reason = controller.filter_velocity(
        desired,
        clear_status(),
        make_pose(x=0.20, y=0.0, yaw=0.35),
        now_s=1.2,
    )

    assert reason == 'avoid_forward_return_heading'
    assert output.linear.x == 0.2
    assert output.angular.z != 0.0


def test_complete_bypass_minimum_forward_time_ignores_side_obstacle():
    controller = CompleteBypassController(
        CompleteBypassConfig(
            bypass_forward_mps=0.2,
            return_angular_z=0.25,
            avoid_min_forward_s=1.0,
            side_clear_hold_s=0.0,
        )
    )
    desired = make_twist(0.4)

    controller.filter_velocity(
        desired,
        blocked_status(left_distance_m=2.0, right_distance_m=1.0),
        make_pose(yaw=0.0),
        now_s=0.0,
    )
    output, reason = controller.filter_velocity(
        desired,
        side_obstacle_status('right'),
        make_pose(x=0.08, y=0.0, yaw=0.35),
        now_s=0.4,
    )

    assert reason == 'avoid_forward_min_forward'
    assert controller.state.phase == 'avoid_forward'
    assert output.linear.x == 0.2
    assert output.angular.z == 0.0


def test_complete_bypass_returns_heading_while_still_moving_forward_when_side_is_lost():
    controller = CompleteBypassController(
        CompleteBypassConfig(
            bypass_forward_mps=0.2,
            return_angular_z=0.25,
            return_heading_tolerance_rad=0.08,
        )
    )
    desired = make_twist(0.4)

    controller.filter_velocity(
        desired,
        blocked_status(left_distance_m=2.0, right_distance_m=1.0),
        make_pose(yaw=0.0),
    )
    controller.filter_velocity(
        desired,
        side_obstacle_status('right'),
        make_pose(x=0.0, y=0.0, yaw=0.35),
    )
    output, reason = controller.filter_velocity(
        desired,
        clear_status(),
        make_pose(x=0.10, y=0.15, yaw=0.35),
    )

    assert reason == 'avoid_forward_return_heading'
    assert controller.state.phase == 'avoid_forward'
    assert output.linear.x == 0.2
    assert output.angular.z != 0.0


def test_complete_bypass_finishes_after_heading_is_restored_without_returning_to_line():
    controller = CompleteBypassController(
        CompleteBypassConfig(
            normal_forward_mps=0.4,
            bypass_forward_mps=0.2,
            return_heading_tolerance_rad=0.08,
        )
    )
    desired = make_twist(0.4)

    controller.filter_velocity(
        desired,
        blocked_status(left_distance_m=2.0, right_distance_m=1.0),
        make_pose(yaw=0.0),
    )
    controller.filter_velocity(
        desired,
        side_obstacle_status('right'),
        make_pose(x=0.0, y=0.0, yaw=0.35),
    )
    output, reason = controller.filter_velocity(
        desired,
        clear_status(),
        make_pose(x=0.30, y=0.25, yaw=0.03),
    )

    assert reason == 'clear'
    assert controller.state.phase == 'cruise'
    assert output.linear.x == 0.4


def test_complete_bypass_lost_side_obstacle_returns_heading_without_reacquire_phase():
    controller = CompleteBypassController(
        CompleteBypassConfig(
            bypass_forward_mps=0.2,
            return_angular_z=0.25,
            return_heading_tolerance_rad=0.08,
        )
    )
    desired = make_twist(0.4)

    controller.filter_velocity(desired, blocked_status(left_distance_m=2.0, right_distance_m=1.0), make_pose())
    controller.filter_velocity(desired, side_obstacle_status('right'), make_pose(yaw=0.3))
    output, reason = controller.filter_velocity(desired, clear_status(), make_pose(x=0.1, y=0.2, yaw=0.3))

    assert reason == 'avoid_forward_return_heading'
    assert controller.state.phase == 'avoid_forward'
    assert output.linear.x == 0.2
    assert output.angular.z != 0.0
    assert controller.state.reacquire_turn_rad == 0.0


def test_complete_bypass_ignores_lateral_offset_and_only_requires_heading_restored():
    controller = CompleteBypassController(
        CompleteBypassConfig(
            normal_forward_mps=0.4,
            bypass_forward_mps=0.2,
            return_angular_z=0.25,
            return_heading_tolerance_rad=0.08,
        )
    )
    desired = make_twist(0.4)
    controller.state.phase = 'avoid_forward'
    controller.state.origin_x = 0.0
    controller.state.origin_y = 0.0
    controller.state.origin_yaw = 0.0
    controller.state.direction = 1
    controller.state.tracked_side = 'right'

    output, reason = controller.filter_velocity(desired, clear_status(), make_pose(x=0.4, y=0.22, yaw=0.2))

    assert reason == 'avoid_forward_return_heading'
    assert controller.state.lateral_offset_m == 0.22
    assert output.linear.x == 0.2
    assert output.angular.z != 0.0

    output, reason = controller.filter_velocity(desired, clear_status(), make_pose(x=0.6, y=0.22, yaw=0.03))

    assert reason == 'clear'
    assert controller.state.phase == 'cruise'
    assert output.linear.x == 0.4


def test_complete_bypass_return_heading_sign_is_reversed_from_yaw_error():
    controller = CompleteBypassController(
        CompleteBypassConfig(
            return_angular_z=0.25,
            return_heading_tolerance_rad=0.08,
        )
    )
    desired = make_twist(0.4)
    controller.state.phase = 'return_heading'
    controller.state.target_yaw = 0.0

    output, reason = controller.filter_velocity(desired, clear_status(), make_pose(yaw=-0.4))

    assert reason == 'avoid_forward_return_heading'
    assert controller.state.yaw_error_rad > 0.0
    assert output.linear.x > 0.0
    assert output.angular.z < 0.0


def test_complete_bypass_stops_when_pose_is_missing():
    controller = CompleteBypassController(CompleteBypassConfig())
    output, reason = controller.filter_velocity(make_twist(0.4), blocked_status(), None)

    assert reason == 'no_pose'
    assert output.linear.x == 0.0
    assert output.angular.z == 0.0


def test_complete_bypass_stops_when_commanded_motion_does_not_change_pose():
    controller = CompleteBypassController(
        CompleteBypassConfig(
            normal_forward_mps=0.4,
            pose_stall_timeout_s=0.5,
            pose_stall_distance_epsilon_m=0.02,
            pose_stall_yaw_epsilon_rad=0.02,
        )
    )
    desired = make_twist(0.4)

    output, reason = controller.filter_velocity(desired, clear_status(), make_pose(), now_s=0.0)
    assert reason == 'clear'
    assert output.linear.x == 0.4

    output, reason = controller.filter_velocity(desired, clear_status(), make_pose(), now_s=0.6)

    assert reason == 'pose_stalled'
    assert controller.state.phase == 'pose_stalled_stop'
    assert output.linear.x == 0.0
    assert output.angular.z == 0.0


def test_guard_status_json_includes_line_following_debug_state():
    output = make_twist(0.1, -0.2)
    bypass_state = CompleteBypassState(
        phase='avoid_forward',
        target_yaw=0.0,
        tracked_side='right',
        pass_distance_m=0.48,
        forward_offset_m=0.42,
        lateral_offset_m=0.18,
        heading_error_rad=-0.31,
        yaw_error_rad=-0.31,
        reacquire_turn_rad=1.2,
        pose_stalled=False,
    )

    payload = json.loads(
        _status_json(
            status=side_obstacle_status('right'),
            output=output,
            dry_run=False,
            reason='avoid_forward_wait_sides_clear',
            cmd_age_s=0.1,
            depth_age_s=0.02,
            pose_age_s=0.01,
            bypass_state=bypass_state,
        )
    )

    assert payload['avoidance_phase'] == 'avoid_forward'
    assert payload['tracked_side'] == 'right'
    assert payload['forward_offset_m'] == 0.42
    assert payload['lateral_offset_m'] == 0.18
    assert payload['heading_error_rad'] == -0.31
    assert payload['reacquire_turn_rad'] == 1.2
    assert payload['pose_stalled'] is False


def test_guard_releases_bypass_to_fresh_command_only_after_returning_clear():
    assert obstacle_guard_node._should_release_bypass_to_fresh_cmd(
        using_takeover_cmd=False,
        bypass_phase='avoid_forward',
        last_reason='avoid_forward_return_heading',
        status=clear_status(),
    ) is True
    assert obstacle_guard_node._should_release_bypass_to_fresh_cmd(
        using_takeover_cmd=False,
        bypass_phase='exit_forward_hold',
        last_reason='avoid_forward_exit_hold',
        status=clear_status(),
    ) is True
    assert obstacle_guard_node._should_release_bypass_to_fresh_cmd(
        using_takeover_cmd=True,
        bypass_phase='avoid_forward',
        last_reason='avoid_forward_return_heading',
        status=clear_status(),
    ) is False
    assert obstacle_guard_node._should_release_bypass_to_fresh_cmd(
        using_takeover_cmd=False,
        bypass_phase='avoid_forward',
        last_reason='avoid_forward_min_forward',
        status=clear_status(),
    ) is False
    assert obstacle_guard_node._should_release_bypass_to_fresh_cmd(
        using_takeover_cmd=False,
        bypass_phase='avoid_forward',
        last_reason='avoid_forward_return_heading',
        status=side_obstacle_status('right'),
    ) is False


def test_complete_bypass_steers_away_when_left_side_has_obstacle():
    controller = CompleteBypassController(
        CompleteBypassConfig(
            bypass_forward_mps=0.2,
            return_angular_z=0.25,
            return_heading_tolerance_rad=0.08,
            avoid_min_forward_s=0.0,
            side_clear_hold_s=0.0,
        )
    )
    desired = make_twist(0.4)

    controller.filter_velocity(
        desired,
        blocked_status(left_distance_m=2.0, right_distance_m=1.0),
        make_pose(yaw=0.0),
    )
    controller.filter_velocity(
        desired,
        side_obstacle_status('right'),
        make_pose(x=0.0, y=0.0, yaw=0.35),
    )
    output, reason = controller.filter_velocity(
        desired,
        side_obstacle_status('left'),
        make_pose(x=0.10, y=0.15, yaw=0.35),
    )

    assert reason == 'avoid_forward_side_steer_right'
    assert controller.state.phase == 'avoid_forward'
    assert output.linear.x == 0.2
    assert output.angular.z < 0.0


def test_complete_bypass_prioritizes_left_invalid_side_over_right_close_obstacle():
    controller = CompleteBypassController(
        CompleteBypassConfig(
            bypass_forward_mps=0.2,
            return_angular_z=0.25,
            return_heading_tolerance_rad=0.08,
            avoid_min_forward_s=0.0,
            side_clear_hold_s=0.0,
        )
    )
    desired = make_twist(0.4)
    controller.state.phase = 'avoid_forward'
    controller.state.origin_x = 0.0
    controller.state.origin_y = 0.0
    controller.state.origin_yaw = 0.0
    controller.state.direction = 1
    controller.state.tracked_side = 'right'

    output, reason = controller.filter_velocity(
        desired,
        side_invalid_and_valid_obstacle_status(invalid_side='left'),
        make_pose(x=0.10, y=0.15, yaw=0.35),
    )

    assert reason == 'avoid_forward_side_steer_right'
    assert controller.state.phase == 'avoid_forward'
    assert output.linear.x == 0.2
    assert output.angular.z < 0.0


def test_complete_bypass_prioritizes_right_invalid_side_over_left_close_obstacle():
    controller = CompleteBypassController(
        CompleteBypassConfig(
            bypass_forward_mps=0.2,
            return_angular_z=0.25,
            return_heading_tolerance_rad=0.08,
            avoid_min_forward_s=0.0,
            side_clear_hold_s=0.0,
        )
    )
    desired = make_twist(0.4)
    controller.state.phase = 'avoid_forward'
    controller.state.origin_x = 0.0
    controller.state.origin_y = 0.0
    controller.state.origin_yaw = 0.0
    controller.state.direction = -1
    controller.state.tracked_side = 'left'

    output, reason = controller.filter_velocity(
        desired,
        side_invalid_and_valid_obstacle_status(invalid_side='right'),
        make_pose(x=0.10, y=-0.15, yaw=-0.35),
    )

    assert reason == 'avoid_forward_side_steer_left'
    assert controller.state.phase == 'avoid_forward'
    assert output.linear.x == 0.2
    assert output.angular.z > 0.0


def test_complete_bypass_does_not_finish_while_side_obstacle_is_visible():
    controller = CompleteBypassController(
        CompleteBypassConfig(
            normal_forward_mps=0.4,
            bypass_forward_mps=0.2,
            return_heading_tolerance_rad=0.08,
            avoid_min_forward_s=0.0,
            side_clear_hold_s=0.0,
        )
    )
    desired = make_twist(0.4)

    controller.filter_velocity(
        desired,
        blocked_status(left_distance_m=2.0, right_distance_m=1.0),
        make_pose(yaw=0.0),
    )
    controller.filter_velocity(
        desired,
        side_obstacle_status('right'),
        make_pose(yaw=0.35),
    )
    output, reason = controller.filter_velocity(
        desired,
        side_obstacle_status('left'),
        make_pose(x=0.30, y=0.25, yaw=0.03),
    )

    assert reason == 'avoid_forward_side_steer_right'
    assert controller.state.phase == 'avoid_forward'
    assert output.linear.x == 0.2
    assert output.angular.z < 0.0


def test_complete_bypass_holds_front_turn_clear_before_returning_heading():
    controller = CompleteBypassController(
        CompleteBypassConfig(
            bypass_forward_mps=0.2,
            return_angular_z=0.25,
            return_heading_tolerance_rad=0.08,
            avoid_min_forward_s=0.0,
            side_clear_hold_s=1.0,
        )
    )
    desired = make_twist(0.4)

    controller.filter_velocity(
        desired,
        blocked_status(left_distance_m=2.0, right_distance_m=1.0),
        make_pose(yaw=0.0),
        now_s=0.0,
    )
    output, reason = controller.filter_velocity(
        desired,
        clear_status(),
        make_pose(x=0.10, y=0.15, yaw=0.35),
        now_s=1.2,
    )

    assert reason == 'front_turn_clear_hold'
    assert output.linear.x == 0.0
    assert output.angular.z > 0.0

    output, reason = controller.filter_velocity(
        desired,
        clear_status(),
        make_pose(x=0.30, y=0.15, yaw=0.35),
        now_s=2.3,
    )

    assert reason == 'avoid_forward_return_heading'
    assert output.linear.x == 0.2
    assert output.angular.z != 0.0


def test_complete_bypass_front_turn_holds_one_second_after_all_zones_clear():
    controller = CompleteBypassController(
        CompleteBypassConfig(
            bypass_angular_z=0.25,
            return_angular_z=0.25,
            return_heading_tolerance_rad=0.08,
            avoid_min_forward_s=0.0,
            side_clear_hold_s=1.0,
        )
    )
    desired = make_twist(0.4)

    controller.filter_velocity(
        desired,
        blocked_status(left_distance_m=2.0, right_distance_m=1.0),
        make_pose(yaw=0.0),
        now_s=0.0,
    )
    output, reason = controller.filter_velocity(
        desired,
        clear_status(),
        make_pose(yaw=0.30),
        now_s=0.4,
    )

    assert reason == 'front_turn_clear_hold'
    assert controller.state.phase == 'turn_away'
    assert output.linear.x == 0.0
    assert output.angular.z > 0.0

    output, reason = controller.filter_velocity(
        desired,
        clear_status(),
        make_pose(x=0.05, y=0.02, yaw=0.30),
        now_s=1.6,
    )

    assert reason == 'avoid_forward_return_heading'
    assert controller.state.phase == 'avoid_forward'
    assert output.linear.x == 0.2
    assert output.angular.z != 0.0


def test_complete_bypass_side_turn_holds_one_second_after_obstacle_clears():
    controller = CompleteBypassController(
        CompleteBypassConfig(
            bypass_forward_mps=0.2,
            return_angular_z=0.25,
            return_heading_tolerance_rad=0.08,
            avoid_min_forward_s=0.0,
            side_clear_hold_s=1.0,
        )
    )
    desired = make_twist(0.4)

    controller.filter_velocity(
        desired,
        blocked_status(left_distance_m=2.0, right_distance_m=1.0),
        make_pose(yaw=0.0),
        now_s=0.0,
    )
    output, reason = controller.filter_velocity(
        desired,
        side_obstacle_status('left'),
        make_pose(x=0.05, y=0.03, yaw=0.30),
        now_s=1.2,
    )
    assert reason == 'avoid_forward_side_steer_right'
    assert output.linear.x == 0.2
    assert output.angular.z < 0.0

    output, reason = controller.filter_velocity(
        desired,
        clear_status(),
        make_pose(x=0.10, y=0.05, yaw=0.30),
        now_s=1.5,
    )

    assert reason == 'avoid_forward_turn_clear_hold'
    assert output.linear.x == 0.2
    assert output.angular.z < 0.0

    output, reason = controller.filter_velocity(
        desired,
        clear_status(),
        make_pose(x=0.30, y=0.10, yaw=0.30),
        now_s=2.6,
    )

    assert reason == 'avoid_forward_return_heading'
    assert output.linear.x == 0.2
    assert output.angular.z != 0.0


def test_complete_bypass_exits_half_second_after_heading_is_restored():
    controller = CompleteBypassController(
        CompleteBypassConfig(
            normal_forward_mps=0.4,
            bypass_forward_mps=0.2,
            return_heading_tolerance_rad=0.08,
            avoid_min_forward_s=0.0,
            side_clear_hold_s=0.0,
        )
    )
    desired = make_twist(0.4)

    controller.filter_velocity(
        desired,
        blocked_status(left_distance_m=2.0, right_distance_m=1.0),
        make_pose(yaw=0.0),
        now_s=0.0,
    )
    output, reason = controller.filter_velocity(
        desired,
        clear_status(),
        make_pose(x=0.20, y=0.10, yaw=0.03),
        now_s=1.2,
    )

    assert reason == 'avoid_forward_exit_hold'
    assert controller.state.phase == 'exit_forward_hold'
    assert output.linear.x == 0.2
    assert output.angular.z == 0.0

    output, reason = controller.filter_velocity(
        desired,
        clear_status(),
        make_pose(x=0.50, y=0.10, yaw=0.02),
        now_s=1.6,
    )

    assert reason == 'avoid_forward_exit_hold'
    assert controller.state.phase == 'exit_forward_hold'
    assert output.linear.x == 0.2

    output, reason = controller.filter_velocity(
        desired,
        clear_status(),
        make_pose(x=0.80, y=0.10, yaw=0.02),
        now_s=1.71,
    )

    assert reason == 'clear'
    assert controller.state.phase == 'cruise'
    assert output.linear.x == 0.4


def test_complete_bypass_exit_hold_is_cancelled_by_side_obstacle():
    controller = CompleteBypassController(
        CompleteBypassConfig(
            bypass_forward_mps=0.2,
            return_angular_z=0.25,
            return_heading_tolerance_rad=0.08,
            avoid_min_forward_s=0.0,
            side_clear_hold_s=0.0,
        )
    )
    desired = make_twist(0.4)

    controller.filter_velocity(
        desired,
        blocked_status(left_distance_m=2.0, right_distance_m=1.0),
        make_pose(yaw=0.0),
        now_s=0.0,
    )
    output, reason = controller.filter_velocity(
        desired,
        clear_status(),
        make_pose(x=0.20, y=0.10, yaw=0.03),
        now_s=1.2,
    )
    assert reason == 'avoid_forward_exit_hold'

    output, reason = controller.filter_velocity(
        desired,
        side_obstacle_status('right'),
        make_pose(x=0.30, y=0.10, yaw=0.03),
        now_s=1.5,
    )

    assert reason == 'avoid_forward_side_steer_left'
    assert controller.state.phase == 'avoid_forward'
    assert output.linear.x == 0.2
    assert output.angular.z > 0.0


def test_complete_bypass_side_obstacle_resets_side_clear_hold():
    controller = CompleteBypassController(
        CompleteBypassConfig(
            bypass_forward_mps=0.2,
            return_angular_z=0.25,
            return_heading_tolerance_rad=0.08,
            avoid_min_forward_s=0.0,
            side_clear_hold_s=1.0,
        )
    )
    desired = make_twist(0.4)

    controller.filter_velocity(
        desired,
        blocked_status(left_distance_m=2.0, right_distance_m=1.0),
        make_pose(yaw=0.0),
        now_s=0.0,
    )
    controller.filter_velocity(
        desired,
        clear_status(),
        make_pose(x=0.10, y=0.15, yaw=0.35),
        now_s=1.2,
    )
    output, reason = controller.filter_velocity(
        desired,
        side_obstacle_status('left'),
        make_pose(x=0.12, y=0.16, yaw=0.35),
        now_s=1.5,
    )

    assert reason == 'avoid_forward_side_steer_right'
    assert output.linear.x == 0.2
    assert output.angular.z < 0.0

    output, reason = controller.filter_velocity(
        desired,
        clear_status(),
        make_pose(x=0.20, y=0.16, yaw=0.35),
        now_s=2.2,
    )

    assert reason == 'avoid_forward_turn_clear_hold'
    assert output.angular.z < 0.0

    output, reason = controller.filter_velocity(
        desired,
        clear_status(),
        make_pose(x=0.35, y=0.16, yaw=0.35),
        now_s=2.6,
    )

    assert reason == 'avoid_forward_turn_clear_hold'
    assert output.angular.z < 0.0

    output, reason = controller.filter_velocity(
        desired,
        clear_status(),
        make_pose(x=0.45, y=0.16, yaw=0.35),
        now_s=3.3,
    )

    assert reason == 'avoid_forward_return_heading'
    assert output.angular.z != 0.0


def test_complete_bypass_resumes_avoidance_when_original_side_reappears_during_return():
    controller = CompleteBypassController(
        CompleteBypassConfig(
            bypass_forward_mps=0.2,
            return_angular_z=0.25,
            return_heading_tolerance_rad=0.08,
            avoid_min_forward_s=0.0,
            side_clear_hold_s=0.0,
        )
    )
    desired = make_twist(0.4)

    controller.filter_velocity(
        desired,
        blocked_status(left_distance_m=2.0, right_distance_m=1.0),
        make_pose(yaw=0.0),
    )
    output, reason = controller.filter_velocity(
        desired,
        clear_status(),
        make_pose(x=0.10, y=0.15, yaw=0.35),
    )
    assert reason == 'avoid_forward_return_heading'
    assert output.angular.z != 0.0

    output, reason = controller.filter_velocity(
        desired,
        side_obstacle_status('right'),
        make_pose(x=0.12, y=0.18, yaw=0.30),
    )

    assert reason == 'avoid_forward_side_steer_left'
    assert controller.state.phase == 'avoid_forward'
    assert output.linear.x == 0.2
    assert output.angular.z > 0.0
