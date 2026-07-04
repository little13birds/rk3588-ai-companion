from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional


AVOIDANCE_REASONS = {
    'avoid_forward',
    'avoid_forward_min_forward',
    'avoid_forward_side_steer_left',
    'avoid_forward_side_steer_right',
    'avoid_forward_side_visible',
    'avoid_forward_side_clear_hold',
    'avoid_forward_turn_clear_hold',
    'avoid_forward_wait_sides_clear',
    'avoid_forward_return_heading',
    'avoid_forward_exit_hold',
    'front_turn_clear_hold',
    'side_follow_forward',
    'reacquire_side',
    'return_to_line',
    'return_heading',
    'pass_obstacle',
}


@dataclass(frozen=True)
class ObstacleWebStatus:
    state: Optional[str]
    reason: str
    front_distance_m: Optional[float]
    left_distance_m: Optional[float]
    right_distance_m: Optional[float]
    front_blocked: bool
    left_clear: bool
    right_clear: bool
    dry_run: bool
    output_linear_x: float
    output_angular_z: float
    cmd_age_s: Optional[float]
    depth_age_s: Optional[float]
    pose_age_s: Optional[float] = None
    avoidance_phase: Optional[str] = None
    target_yaw_rad: Optional[float] = None
    yaw_error_rad: Optional[float] = None
    bypass_distance_m: Optional[float] = None
    tracked_side: Optional[str] = None
    forward_offset_m: Optional[float] = None
    lateral_offset_m: Optional[float] = None
    heading_error_rad: Optional[float] = None
    reacquire_turn_rad: Optional[float] = None
    pose_stalled: bool = False
    front_valid_fraction: Optional[float] = None
    front_invalid_fraction: Optional[float] = None
    left_valid_fraction: Optional[float] = None
    left_invalid_fraction: Optional[float] = None
    right_valid_fraction: Optional[float] = None
    right_invalid_fraction: Optional[float] = None
    last_update_s: float = 0.0


def parse_guard_status(data: str, now_s: float) -> ObstacleWebStatus:
    try:
        payload = json.loads(data)
    except (TypeError, ValueError):
        return waiting_status('invalid_status', now_s)

    front_distance_m = _optional_float(payload.get('front_distance_m'))
    left_distance_m = _optional_float(payload.get('left_distance_m'))
    right_distance_m = _optional_float(payload.get('right_distance_m'))

    return ObstacleWebStatus(
        state=_optional_str(payload.get('state')),
        reason=str(payload.get('reason') or 'unknown'),
        front_distance_m=front_distance_m,
        left_distance_m=left_distance_m,
        right_distance_m=right_distance_m,
        front_blocked=front_distance_m is None or bool(payload.get('front_blocked', False)),
        left_clear=left_distance_m is not None and bool(payload.get('left_clear', False)),
        right_clear=right_distance_m is not None and bool(payload.get('right_clear', False)),
        dry_run=bool(payload.get('dry_run', True)),
        output_linear_x=float(payload.get('output_linear_x') or 0.0),
        output_angular_z=float(payload.get('output_angular_z') or 0.0),
        cmd_age_s=_optional_float(payload.get('cmd_age_s')),
        depth_age_s=_optional_float(payload.get('depth_age_s')),
        pose_age_s=_optional_float(payload.get('pose_age_s')),
        avoidance_phase=_optional_str(payload.get('avoidance_phase')),
        target_yaw_rad=_optional_float(payload.get('target_yaw_rad')),
        yaw_error_rad=_optional_float(payload.get('yaw_error_rad')),
        bypass_distance_m=_optional_float(payload.get('bypass_distance_m')),
        tracked_side=_optional_str(payload.get('tracked_side')),
        forward_offset_m=_optional_float(payload.get('forward_offset_m')),
        lateral_offset_m=_optional_float(payload.get('lateral_offset_m')),
        heading_error_rad=_optional_float(payload.get('heading_error_rad')),
        reacquire_turn_rad=_optional_float(payload.get('reacquire_turn_rad')),
        pose_stalled=bool(payload.get('pose_stalled', False)),
        front_valid_fraction=_optional_float(payload.get('front_valid_fraction')),
        front_invalid_fraction=_optional_float(payload.get('front_invalid_fraction')),
        left_valid_fraction=_optional_float(payload.get('left_valid_fraction')),
        left_invalid_fraction=_optional_float(payload.get('left_invalid_fraction')),
        right_valid_fraction=_optional_float(payload.get('right_valid_fraction')),
        right_invalid_fraction=_optional_float(payload.get('right_invalid_fraction')),
        last_update_s=float(now_s),
    )


def waiting_status(reason: str, now_s: float) -> ObstacleWebStatus:
    return ObstacleWebStatus(
        state=None,
        reason=reason,
        front_distance_m=None,
        left_distance_m=None,
        right_distance_m=None,
        front_blocked=False,
        left_clear=False,
        right_clear=False,
        dry_run=True,
        output_linear_x=0.0,
        output_angular_z=0.0,
        cmd_age_s=None,
        depth_age_s=None,
        pose_age_s=None,
        avoidance_phase=None,
        target_yaw_rad=None,
        yaw_error_rad=None,
        bypass_distance_m=None,
        tracked_side=None,
        forward_offset_m=None,
        lateral_offset_m=None,
        heading_error_rad=None,
        reacquire_turn_rad=None,
        pose_stalled=False,
        front_valid_fraction=None,
        front_invalid_fraction=None,
        left_valid_fraction=None,
        left_invalid_fraction=None,
        right_valid_fraction=None,
        right_invalid_fraction=None,
        last_update_s=float(now_s),
    )


def obstacle_level(status: ObstacleWebStatus) -> str:
    if status.front_blocked:
        return 'danger'
    if status.reason in AVOIDANCE_REASONS:
        return 'warning'
    if status.state == 'clear':
        return 'clear'
    if status.reason in {'no_depth', 'depth_stale', 'no_cmd', 'cmd_stale', 'invalid_status', 'waiting'}:
        return 'waiting'
    return 'warning'


def status_message(status: ObstacleWebStatus) -> str:
    level = obstacle_level(status)
    if status.reason == 'pose_stalled':
        return '位姿无变化，已停车'
    if status.reason == 'avoid_forward_min_forward':
        return '避障前进，延迟回正'
    if status.reason == 'avoid_forward_side_steer_left':
        return '侧边避让，右侧障碍'
    if status.reason == 'avoid_forward_side_steer_right':
        return '侧边避让，左侧障碍'
    if status.reason == 'avoid_forward_side_clear_hold':
        return '侧边清空保持中'
    if status.reason == 'avoid_forward_turn_clear_hold':
        return '障碍清空，延迟停止转弯'
    if status.reason == 'avoid_forward_side_visible':
        return '避障前进，侧边仍有障碍'
    if status.reason == 'avoid_forward_wait_sides_clear':
        return '避障中，等待两侧清空'
    if status.reason == 'avoid_forward_return_heading':
        return '避障回正中'
    if status.reason == 'avoid_forward_exit_hold':
        return '回正完成，保持前进确认'
    if status.reason == 'avoid_forward':
        return '避障前进'
    if status.reason == 'side_follow_forward':
        return '侧边跟随绕行'
    if status.reason == 'reacquire_side':
        return '低速找回侧边障碍'
    if status.reason == 'return_to_line':
        return '回归原行驶线'
    if status.reason == 'turn_away':
        return '转向避障'
    if status.reason == 'front_turn_clear_hold':
        return '前方清空，延迟停止转弯'
    if status.reason == 'pass_obstacle':
        return '绕障前进'
    if status.reason == 'return_heading':
        return '回正航向'
    if status.reason == 'blocked_no_side':
        return '两侧不可绕行'
    if status.reason == 'no_pose':
        return '等待融合位姿'
    if level == 'danger':
        return '前方有障碍'
    if level == 'clear':
        return '正常巡航，前方安全'
    if status.reason == 'no_depth':
        return '等待深度图'
    if status.reason == 'depth_stale':
        return '深度图超时'
    if status.reason == 'no_cmd':
        return '等待速度指令'
    if status.reason == 'cmd_stale':
        return '速度指令超时'
    if status.reason == 'invalid_status':
        return '状态解析失败'
    return '等待数据'


def status_payload(
    status: ObstacleWebStatus,
    *,
    camera_fps: float,
    stream_fps: float,
    image_width: int,
    image_height: int,
    depth_fps: float = 0.0,
) -> dict:
    return {
        'state': {
            'level': obstacle_level(status),
            'guard_state': status.state,
            'reason': status.reason,
            'message': status_message(status),
        },
        'zones': {
            'left': {
                'distance_m': _round_optional(status.left_distance_m),
                'clear': status.left_clear,
                'valid_fraction': _round_optional(status.left_valid_fraction),
                'invalid_fraction': _round_optional(status.left_invalid_fraction),
            },
            'front': {
                'distance_m': _round_optional(status.front_distance_m),
                'blocked': status.front_blocked,
                'valid_fraction': _round_optional(status.front_valid_fraction),
                'invalid_fraction': _round_optional(status.front_invalid_fraction),
            },
            'right': {
                'distance_m': _round_optional(status.right_distance_m),
                'clear': status.right_clear,
                'valid_fraction': _round_optional(status.right_valid_fraction),
                'invalid_fraction': _round_optional(status.right_invalid_fraction),
            },
        },
        'output': {
            'linear_x': round(status.output_linear_x, 3),
            'angular_z': round(status.output_angular_z, 3),
        },
        'mode': {
            'dry_run': status.dry_run,
        },
        'fps': {
            'camera': round(float(camera_fps), 2),
            'depth': round(float(depth_fps), 2),
            'stream': round(float(stream_fps), 2),
        },
        'image': {
            'width': int(image_width),
            'height': int(image_height),
        },
        'age': {
            'cmd_s': _round_optional(status.cmd_age_s),
            'depth_s': _round_optional(status.depth_age_s),
            'pose_s': _round_optional(status.pose_age_s),
        },
        'avoidance': {
            'phase': status.avoidance_phase,
            'target_yaw_rad': _round_optional(status.target_yaw_rad),
            'yaw_error_rad': _round_optional(status.yaw_error_rad),
            'bypass_distance_m': _round_optional(status.bypass_distance_m),
            'tracked_side': status.tracked_side,
            'forward_offset_m': _round_optional(status.forward_offset_m),
            'lateral_offset_m': _round_optional(status.lateral_offset_m),
            'heading_error_rad': _round_optional(status.heading_error_rad),
            'reacquire_turn_rad': _round_optional(status.reacquire_turn_rad),
            'pose_stalled': status.pose_stalled,
        },
        'last_update_s': round(status.last_update_s, 3),
    }


def _optional_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _round_optional(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), 3)
