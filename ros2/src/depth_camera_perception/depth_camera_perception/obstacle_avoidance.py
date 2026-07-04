from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .depth_utils import normalize_depth_to_meters


SIDE_INVALID_PRIORITY_FRACTION = 0.90


@dataclass(frozen=True)
class ObstacleConfig:
    danger_distance_m: float = 0.55
    clear_distance_m: float = 0.80
    min_depth_m: float = 0.15
    max_depth_m: float = 5.0
    roi_top_fraction: float = 0.40
    roi_bottom_fraction: float = 0.85
    front_width_fraction: float = 0.50
    distance_percentile: float = 10.0
    front_invalid_depth_block_fraction: float = 0.35
    side_invalid_depth_block_fraction: float = 0.90
    invalid_depth_block_fraction: Optional[float] = None


@dataclass(frozen=True)
class ObstacleStatus:
    state: str
    front_distance_m: Optional[float]
    left_distance_m: Optional[float]
    right_distance_m: Optional[float]
    front_blocked: bool
    left_clear: bool
    right_clear: bool
    front_valid_fraction: float = 0.0
    front_invalid_fraction: float = 1.0
    left_valid_fraction: float = 0.0
    left_invalid_fraction: float = 1.0
    right_valid_fraction: float = 0.0
    right_invalid_fraction: float = 1.0


@dataclass(frozen=True)
class ZoneDepthStats:
    distance_m: Optional[float]
    valid_fraction: float
    invalid_fraction: float


@dataclass(frozen=True)
class ObstaclePose:
    x: float
    y: float
    yaw: float


@dataclass(frozen=True)
class CompleteBypassConfig:
    normal_forward_mps: float = 0.40
    bypass_forward_mps: float = 0.20
    bypass_angular_z: float = 0.25
    return_angular_z: float = 0.25
    return_heading_angular_sign: float = -1.0
    bypass_forward_distance_m: float = 0.50
    return_heading_tolerance_rad: float = 0.08
    line_return_tolerance_m: float = 0.08
    reacquire_forward_mps: float = 0.10
    reacquire_angular_z: float = 0.20
    reacquire_max_yaw_rad: float = math.pi / 2.0
    avoid_min_forward_s: float = 1.0
    side_clear_hold_s: float = 1.0
    exit_forward_hold_s: float = 0.5
    pose_stall_timeout_s: float = 1.0
    pose_stall_distance_epsilon_m: float = 0.02
    pose_stall_yaw_epsilon_rad: float = 0.03


@dataclass
class CompleteBypassState:
    phase: str = 'cruise'
    target_yaw: Optional[float] = None
    direction: int = 0
    tracked_side: Optional[str] = None
    origin_x: Optional[float] = None
    origin_y: Optional[float] = None
    origin_yaw: Optional[float] = None
    pass_start_x: Optional[float] = None
    pass_start_y: Optional[float] = None
    pass_distance_m: float = 0.0
    forward_offset_m: Optional[float] = None
    lateral_offset_m: Optional[float] = None
    heading_error_rad: Optional[float] = None
    yaw_error_rad: Optional[float] = None
    reacquire_start_yaw: Optional[float] = None
    reacquire_turn_rad: float = 0.0
    bypass_start_s: Optional[float] = None
    side_clear_start_s: Optional[float] = None
    turn_clear_start_s: Optional[float] = None
    hold_turn_direction: int = 0
    exit_hold_start_s: Optional[float] = None
    pose_stalled: bool = False
    last_motion_x: Optional[float] = None
    last_motion_y: Optional[float] = None
    last_motion_yaw: Optional[float] = None
    last_motion_s: Optional[float] = None


class CompleteBypassController:
    def __init__(self, config: Optional[CompleteBypassConfig] = None):
        self.config = config or CompleteBypassConfig()
        self.state = CompleteBypassState()

    def reset(self) -> None:
        self.state = CompleteBypassState()

    def filter_velocity(
        self,
        desired,
        status: ObstacleStatus,
        pose: Optional[ObstaclePose],
        now_s: Optional[float] = None,
    ):
        if desired.linear.x <= 0.0:
            self.reset()
            return _copy_twist(desired), 'manual'

        if abs(desired.angular.z) > 0.0 and self.state.phase == 'cruise' and not status.front_blocked:
            self.reset()
            return _copy_twist(desired), 'manual'

        if pose is None:
            self.reset()
            return _zero_twist(desired), 'no_pose'

        if self.state.phase == 'pose_stalled_stop':
            return _zero_twist(desired), 'pose_stalled'

        self._update_relative_pose(pose)

        if status.front_blocked:
            output, reason = self._turn_away(desired, status, pose, now_s)
            return self._apply_pose_stall_guard(desired, output, reason, pose, now_s)

        if self.state.phase == 'turn_away':
            if status.left_clear and status.right_clear:
                output = self._turn_clear_hold_output(
                    desired,
                    now_s,
                    direction=self.state.direction,
                    angular_z=self.config.bypass_angular_z,
                    linear_x=0.0,
                )
                if output is not None:
                    return self._apply_pose_stall_guard(
                        desired,
                        output,
                        'front_turn_clear_hold',
                        pose,
                        now_s,
                    )
            else:
                self._clear_turn_hold()
            self.state.phase = 'avoid_forward'
            self.state.reacquire_start_yaw = None
            self.state.reacquire_turn_rad = 0.0

        if self.state.phase in {
            'avoid_forward',
            'exit_forward_hold',
            'side_follow_forward',
            'reacquire_side',
            'return_to_line',
            'return_heading',
        }:
            output, reason = self._avoid_forward(desired, status, pose, now_s)
            return self._apply_pose_stall_guard(desired, output, reason, pose, now_s)

        output = _limit_forward(desired, self.config.normal_forward_mps)
        return self._apply_pose_stall_guard(desired, output, 'clear', pose, now_s)

    def _turn_away(
        self,
        desired,
        status: ObstacleStatus,
        pose: ObstaclePose,
        now_s: Optional[float] = None,
    ):
        if self.state.phase == 'turn_away' and self.state.direction != 0:
            direction = self.state.direction
        else:
            direction = _choose_bypass_direction(status)
        if direction == 0:
            self.reset()
            return _zero_twist(desired), 'blocked_no_side'
        if self.state.phase == 'cruise' or self.state.origin_yaw is None:
            self._begin_bypass(direction, pose, now_s)
        self.state.phase = 'turn_away'
        self.state.direction = direction
        self.state.tracked_side = _tracked_side_from_direction(direction)
        self.state.turn_clear_start_s = None
        self.state.hold_turn_direction = direction
        self.state.exit_hold_start_s = None
        output = _zero_twist(desired)
        output.angular.z = direction * self.config.bypass_angular_z
        return output, 'turn_away'

    def _begin_bypass(
        self,
        direction: int,
        pose: ObstaclePose,
        now_s: Optional[float] = None,
    ) -> None:
        self.state.origin_x = pose.x
        self.state.origin_y = pose.y
        self.state.origin_yaw = pose.yaw
        self.state.target_yaw = pose.yaw
        self.state.direction = direction
        self.state.tracked_side = _tracked_side_from_direction(direction)
        self.state.pass_start_x = pose.x
        self.state.pass_start_y = pose.y
        self.state.pass_distance_m = 0.0
        self.state.forward_offset_m = 0.0
        self.state.lateral_offset_m = 0.0
        self.state.heading_error_rad = 0.0
        self.state.yaw_error_rad = 0.0
        self.state.reacquire_start_yaw = None
        self.state.reacquire_turn_rad = 0.0
        self.state.bypass_start_s = now_s
        self.state.side_clear_start_s = None
        self.state.turn_clear_start_s = None
        self.state.hold_turn_direction = direction
        self.state.exit_hold_start_s = None

    def _avoid_forward(
        self,
        desired,
        status: ObstacleStatus,
        pose: ObstaclePose,
        now_s: Optional[float] = None,
    ):
        self._update_relative_pose(pose)
        target_yaw = self.state.origin_yaw
        if target_yaw is None:
            target_yaw = self.state.target_yaw
        if target_yaw is None:
            self.reset()
            return _limit_forward(desired, self.config.normal_forward_mps), 'clear'

        error = wrap_angle(target_yaw - pose.yaw)
        self.state.heading_error_rad = error
        self.state.yaw_error_rad = error
        side_steer = _side_obstacle_steer_direction(status)
        all_zones_clear = (
            not status.front_blocked
            and status.left_clear
            and status.right_clear
        )

        output = _zero_twist(desired)
        output.linear.x = min(desired.linear.x, self.config.bypass_forward_mps)

        if self._inside_min_forward_window(now_s):
            self.state.side_clear_start_s = None
            self.state.exit_hold_start_s = None
            return output, 'avoid_forward_min_forward'

        if side_steer != 0:
            self.state.side_clear_start_s = None
            self._clear_turn_hold()
            self.state.hold_turn_direction = side_steer
            self.state.exit_hold_start_s = None
            self.state.phase = 'avoid_forward'
            output.angular.z = side_steer * self.config.return_angular_z
            if side_steer > 0:
                return output, 'avoid_forward_side_steer_left'
            return output, 'avoid_forward_side_steer_right'

        if all_zones_clear and self.state.hold_turn_direction != 0:
            held_output = self._turn_clear_hold_output(
                desired,
                now_s,
                direction=self.state.hold_turn_direction,
                angular_z=self.config.return_angular_z,
                linear_x=output.linear.x,
            )
            if held_output is not None:
                self.state.exit_hold_start_s = None
                return held_output, 'avoid_forward_turn_clear_hold'

        if not all_zones_clear:
            self.state.side_clear_start_s = None
            self._clear_turn_hold()
            self.state.exit_hold_start_s = None
            return output, 'avoid_forward_wait_sides_clear'

        if abs(error) <= self.config.return_heading_tolerance_rad:
            return self._exit_forward_hold_or_clear(desired, now_s)

        sign = 1.0 if self.config.return_heading_angular_sign >= 0.0 else -1.0
        self.state.exit_hold_start_s = None
        output.angular.z = sign * math.copysign(self.config.return_angular_z, error)
        return output, 'avoid_forward_return_heading'

    def _turn_clear_hold_output(
        self,
        desired,
        now_s: Optional[float],
        *,
        direction: int,
        angular_z: float,
        linear_x: float,
    ):
        if direction == 0 or now_s is None or self.config.side_clear_hold_s <= 0.0:
            self._clear_turn_hold()
            return None
        if (
            self.state.turn_clear_start_s is None
            or self.state.hold_turn_direction != direction
        ):
            self.state.turn_clear_start_s = now_s
            self.state.hold_turn_direction = direction
        if now_s - self.state.turn_clear_start_s >= self.config.side_clear_hold_s:
            self._clear_turn_hold()
            return None
        output = _zero_twist(desired)
        output.linear.x = min(max(0.0, linear_x), self.config.bypass_forward_mps)
        output.angular.z = direction * angular_z
        return output

    def _clear_turn_hold(self) -> None:
        self.state.turn_clear_start_s = None
        self.state.hold_turn_direction = 0

    def _exit_forward_hold_or_clear(self, desired, now_s: Optional[float]):
        if now_s is None or self.config.exit_forward_hold_s <= 0.0:
            self.reset()
            return _limit_forward(desired, self.config.normal_forward_mps), 'clear'
        if self.state.exit_hold_start_s is None:
            self.state.exit_hold_start_s = now_s
        if now_s - self.state.exit_hold_start_s < self.config.exit_forward_hold_s:
            self.state.phase = 'exit_forward_hold'
            output = _zero_twist(desired)
            output.linear.x = min(desired.linear.x, self.config.bypass_forward_mps)
            return output, 'avoid_forward_exit_hold'
        self.reset()
        return _limit_forward(desired, self.config.normal_forward_mps), 'clear'

    def _inside_min_forward_window(self, now_s: Optional[float]) -> bool:
        if now_s is None or self.state.bypass_start_s is None:
            return False
        return now_s - self.state.bypass_start_s < self.config.avoid_min_forward_s

    def _inside_side_clear_hold(self, now_s: Optional[float]) -> bool:
        if now_s is None:
            return False
        if self.config.side_clear_hold_s <= 0.0:
            return False
        if self.state.side_clear_start_s is None:
            self.state.side_clear_start_s = now_s
            return True
        return now_s - self.state.side_clear_start_s < self.config.side_clear_hold_s

    def _side_follow_forward(self, desired, status: ObstacleStatus, pose: ObstaclePose):
        if not _tracked_side_visible(status, self.state.tracked_side):
            self.state.phase = 'reacquire_side'
            self.state.reacquire_start_yaw = pose.yaw
            self.state.reacquire_turn_rad = 0.0
            return self._reacquire_side(desired, status, pose)
        output = _zero_twist(desired)
        output.linear.x = min(desired.linear.x, self.config.bypass_forward_mps)
        return output, 'side_follow_forward'

    def _reacquire_side(self, desired, status: ObstacleStatus, pose: ObstaclePose):
        if _tracked_side_visible(status, self.state.tracked_side):
            self.state.phase = 'side_follow_forward'
            self.state.reacquire_start_yaw = None
            self.state.reacquire_turn_rad = 0.0
            return self._side_follow_forward(desired, status, pose)
        if self.state.reacquire_start_yaw is None:
            self.state.reacquire_start_yaw = pose.yaw
        self.state.reacquire_turn_rad = abs(wrap_angle(pose.yaw - self.state.reacquire_start_yaw))
        if self.state.reacquire_turn_rad >= self.config.reacquire_max_yaw_rad:
            self.state.phase = 'return_to_line'
            return self._return_to_line(desired, pose)
        output = _zero_twist(desired)
        output.linear.x = min(desired.linear.x, self.config.reacquire_forward_mps)
        output.angular.z = _turn_toward_side(self.state.tracked_side) * self.config.reacquire_angular_z
        return output, 'reacquire_side'

    def _return_to_line(self, desired, pose: ObstaclePose):
        self._update_relative_pose(pose)
        lateral = self.state.lateral_offset_m
        if lateral is None:
            self.state.phase = 'return_heading'
            return self._return_heading(desired, pose)
        if abs(lateral) <= self.config.line_return_tolerance_m:
            self.state.phase = 'return_heading'
            return self._return_heading(desired, pose)
        output = _zero_twist(desired)
        output.linear.x = min(desired.linear.x, self.config.bypass_forward_mps)
        output.angular.z = -math.copysign(self.config.return_angular_z, lateral)
        return output, 'return_to_line'

    def _return_heading(self, desired, pose: ObstaclePose):
        target_yaw = self.state.origin_yaw
        if target_yaw is None:
            target_yaw = self.state.target_yaw
        if target_yaw is None:
            self.reset()
            return _limit_forward(desired, self.config.normal_forward_mps), 'clear'
        error = wrap_angle(target_yaw - pose.yaw)
        self.state.heading_error_rad = error
        self.state.yaw_error_rad = error
        if abs(error) <= self.config.return_heading_tolerance_rad:
            self.reset()
            return _limit_forward(desired, self.config.normal_forward_mps), 'clear'
        output = _zero_twist(desired)
        sign = 1.0 if self.config.return_heading_angular_sign >= 0.0 else -1.0
        output.angular.z = sign * math.copysign(self.config.return_angular_z, error)
        return output, 'return_heading'

    def _update_relative_pose(self, pose: ObstaclePose) -> None:
        if (
            self.state.origin_x is None
            or self.state.origin_y is None
            or self.state.origin_yaw is None
        ):
            return
        dx = pose.x - self.state.origin_x
        dy = pose.y - self.state.origin_y
        heading = self.state.origin_yaw
        self.state.forward_offset_m = math.cos(heading) * dx + math.sin(heading) * dy
        self.state.lateral_offset_m = -math.sin(heading) * dx + math.cos(heading) * dy
        self.state.pass_distance_m = math.hypot(dx, dy)
        self.state.heading_error_rad = wrap_angle(self.state.origin_yaw - pose.yaw)
        self.state.yaw_error_rad = self.state.heading_error_rad

    def _apply_pose_stall_guard(
        self,
        desired,
        output,
        reason: str,
        pose: ObstaclePose,
        now_s: Optional[float],
    ):
        if reason == 'turn_away':
            self._clear_motion_tracking()
            return output, reason
        if now_s is None or not _twist_has_motion(output):
            if not _twist_has_motion(output):
                self._clear_motion_tracking()
            return output, reason
        if self.state.last_motion_s is None:
            self._mark_motion_pose(pose, now_s)
            return output, reason
        moved_distance = math.hypot(
            pose.x - (self.state.last_motion_x or 0.0),
            pose.y - (self.state.last_motion_y or 0.0),
        )
        moved_yaw = abs(wrap_angle(pose.yaw - (self.state.last_motion_yaw or 0.0)))
        if (
            moved_distance >= self.config.pose_stall_distance_epsilon_m
            or moved_yaw >= self.config.pose_stall_yaw_epsilon_rad
        ):
            self._mark_motion_pose(pose, now_s)
            return output, reason
        if now_s - self.state.last_motion_s >= self.config.pose_stall_timeout_s:
            self.state.phase = 'pose_stalled_stop'
            self.state.pose_stalled = True
            return _zero_twist(desired), 'pose_stalled'
        return output, reason

    def _mark_motion_pose(self, pose: ObstaclePose, now_s: float) -> None:
        self.state.last_motion_x = pose.x
        self.state.last_motion_y = pose.y
        self.state.last_motion_yaw = pose.yaw
        self.state.last_motion_s = now_s
        self.state.pose_stalled = False

    def _clear_motion_tracking(self) -> None:
        self.state.last_motion_x = None
        self.state.last_motion_y = None
        self.state.last_motion_yaw = None
        self.state.last_motion_s = None
        self.state.pose_stalled = False


def analyze_depth_zones(depth_image: np.ndarray, config: ObstacleConfig) -> ObstacleStatus:
    depth_m = normalize_depth_to_meters(depth_image)
    height, width = depth_m.shape[:2]
    top = int(height * config.roi_top_fraction)
    bottom = max(top + 1, int(height * config.roi_bottom_fraction))
    center = width // 2
    front_half = int(width * config.front_width_fraction / 2.0)
    front_left = max(0, center - front_half)
    front_right = min(width, center + front_half)

    front = _zone_stats(depth_m[top:bottom, front_left:front_right], config)
    left = _zone_stats(depth_m[top:bottom, 0:front_left], config)
    right = _zone_stats(depth_m[top:bottom, front_right:width], config)

    front_invalid_threshold = _invalid_threshold(
        config,
        config.front_invalid_depth_block_fraction,
    )
    side_invalid_threshold = _invalid_threshold(
        config,
        config.side_invalid_depth_block_fraction,
    )
    front_unreliable = front.invalid_fraction >= front_invalid_threshold
    left_reliable = left.invalid_fraction < side_invalid_threshold
    right_reliable = right.invalid_fraction < side_invalid_threshold

    front_distance = front.distance_m
    left_distance = left.distance_m
    right_distance = right.distance_m
    front_blocked = (
        front_distance is None
        or front_distance < config.danger_distance_m
        or front_unreliable
    )
    left_clear = (
        left_distance is not None
        and left_distance >= config.clear_distance_m
        and left_reliable
    )
    right_clear = (
        right_distance is not None
        and right_distance >= config.clear_distance_m
        and right_reliable
    )

    return ObstacleStatus(
        state='blocked' if front_blocked else 'clear',
        front_distance_m=front_distance,
        left_distance_m=left_distance,
        right_distance_m=right_distance,
        front_blocked=front_blocked,
        left_clear=left_clear,
        right_clear=right_clear,
        front_valid_fraction=front.valid_fraction,
        front_invalid_fraction=front.invalid_fraction,
        left_valid_fraction=left.valid_fraction,
        left_invalid_fraction=left.invalid_fraction,
        right_valid_fraction=right.valid_fraction,
        right_invalid_fraction=right.invalid_fraction,
    )


def guard_velocity(
    desired,
    status: ObstacleStatus,
    *,
    allow_bypass: bool,
    bypass_angular_z: float = 0.25,
    max_forward_mps: float = 0.15,
):
    output = desired.__class__()
    output.linear.x = desired.linear.x
    output.linear.y = desired.linear.y
    output.angular.z = desired.angular.z

    if desired.linear.x <= 0.0 or abs(desired.angular.z) > 0.0:
        return output

    if not status.front_blocked:
        output.linear.x = min(output.linear.x, max_forward_mps)
        return output

    output.linear.x = 0.0
    output.linear.y = 0.0
    output.angular.z = 0.0
    if allow_bypass:
        candidates = []
        if status.left_distance_m is not None:
            candidates.append((status.left_distance_m, bypass_angular_z))
        if status.right_distance_m is not None:
            candidates.append((status.right_distance_m, -bypass_angular_z))
        if candidates:
            output.angular.z = max(candidates, key=lambda item: item[0])[1]
    return output


def wrap_angle(angle: float) -> float:
    wrapped = (float(angle) + math.pi) % (2.0 * math.pi) - math.pi
    if wrapped == -math.pi:
        return math.pi
    return wrapped


def _copy_twist(desired):
    output = desired.__class__()
    output.linear.x = desired.linear.x
    output.linear.y = desired.linear.y
    output.angular.z = desired.angular.z
    return output


def _zero_twist(desired):
    output = desired.__class__()
    output.linear.x = 0.0
    output.linear.y = 0.0
    output.angular.z = 0.0
    return output


def _limit_forward(desired, max_forward_mps: float):
    output = _copy_twist(desired)
    output.linear.x = min(output.linear.x, max_forward_mps)
    return output


def _choose_bypass_direction(status: ObstacleStatus) -> int:
    side_steer = _side_obstacle_steer_direction(status)
    if side_steer != 0:
        return side_steer

    candidates = []
    if status.left_distance_m is not None:
        candidates.append((status.left_distance_m, 1))
    if status.right_distance_m is not None:
        candidates.append((status.right_distance_m, -1))
    if not candidates:
        return 0
    return max(candidates, key=lambda item: item[0])[1]


def _side_obstacle_steer_direction(status: ObstacleStatus) -> int:
    left_obstacle = not status.left_clear
    right_obstacle = not status.right_clear
    if not left_obstacle and not right_obstacle:
        return 0
    if left_obstacle and not right_obstacle:
        return -1
    if right_obstacle and not left_obstacle:
        return 1

    left_invalid = _side_invalid_depth_obstacle(status, 'left')
    right_invalid = _side_invalid_depth_obstacle(status, 'right')
    if left_invalid and not right_invalid:
        return -1
    if right_invalid and not left_invalid:
        return 1

    left_distance = status.left_distance_m
    right_distance = status.right_distance_m
    if left_distance is None and right_distance is None:
        return -1
    if left_distance is None:
        return -1
    if right_distance is None:
        return 1
    if left_distance <= right_distance:
        return -1
    return 1


def _side_invalid_depth_obstacle(status: ObstacleStatus, side: str) -> bool:
    if side == 'left':
        return (
            not status.left_clear
            and (
                status.left_distance_m is None
                or status.left_invalid_fraction >= SIDE_INVALID_PRIORITY_FRACTION
            )
        )
    if side == 'right':
        return (
            not status.right_clear
            and (
                status.right_distance_m is None
                or status.right_invalid_fraction >= SIDE_INVALID_PRIORITY_FRACTION
            )
        )
    return False


def _tracked_side_from_direction(direction: int) -> str:
    return 'right' if direction > 0 else 'left'


def _tracked_side_visible(status: ObstacleStatus, tracked_side: Optional[str]) -> bool:
    if tracked_side == 'left':
        return status.left_distance_m is not None and not status.left_clear
    if tracked_side == 'right':
        return status.right_distance_m is not None and not status.right_clear
    return False


def _turn_toward_side(tracked_side: Optional[str]) -> float:
    if tracked_side == 'left':
        return 1.0
    if tracked_side == 'right':
        return -1.0
    return 0.0


def _twist_has_motion(twist) -> bool:
    return abs(twist.linear.x) > 1e-6 or abs(twist.linear.y) > 1e-6 or abs(twist.angular.z) > 1e-6


def _distance_from_pass_start(state: CompleteBypassState, pose: ObstaclePose) -> float:
    if state.pass_start_x is None or state.pass_start_y is None:
        state.pass_start_x = pose.x
        state.pass_start_y = pose.y
        return 0.0
    return math.hypot(pose.x - state.pass_start_x, pose.y - state.pass_start_y)


def _zone_stats(zone: np.ndarray, config: ObstacleConfig) -> ZoneDepthStats:
    if zone.size == 0:
        return ZoneDepthStats(distance_m=None, valid_fraction=0.0, invalid_fraction=1.0)
    finite = zone[np.isfinite(zone)]
    valid = finite[(finite >= config.min_depth_m) & (finite <= config.max_depth_m)]
    valid_fraction = float(valid.size) / float(zone.size)
    invalid_fraction = 1.0 - valid_fraction
    if valid.size == 0:
        return ZoneDepthStats(
            distance_m=None,
            valid_fraction=valid_fraction,
            invalid_fraction=invalid_fraction,
        )
    return ZoneDepthStats(
        distance_m=float(np.percentile(valid, config.distance_percentile)),
        valid_fraction=valid_fraction,
        invalid_fraction=invalid_fraction,
    )


def _invalid_threshold(config: ObstacleConfig, default_value: float) -> float:
    if config.invalid_depth_block_fraction is not None:
        return min(1.0, max(0.0, float(config.invalid_depth_block_fraction)))
    return min(1.0, max(0.0, float(default_value)))
