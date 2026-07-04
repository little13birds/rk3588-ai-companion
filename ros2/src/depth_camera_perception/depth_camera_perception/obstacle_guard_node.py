from __future__ import annotations

import json
import math
import time
from typing import Optional

import rclpy
from cv_bridge import CvBridge
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from sensor_msgs.msg import Image
from std_msgs.msg import String

from .obstacle_avoidance import (
    CompleteBypassConfig,
    CompleteBypassController,
    CompleteBypassState,
    ObstacleConfig,
    ObstaclePose,
    ObstacleStatus,
    analyze_depth_zones,
    guard_velocity,
)


RELEASABLE_BYPASS_REASONS = {
    'avoid_forward_return_heading',
    'avoid_forward_exit_hold',
}


class ObstacleGuardNode(Node):
    def __init__(self):
        super().__init__('depth_obstacle_guard')
        self.declare_parameter('depth_topic', '/camera/depth/image_raw')
        self.declare_parameter('odom_topic', '/odom_combined')
        self.declare_parameter('input_cmd_vel_topic', '/cmd_vel_raw')
        self.declare_parameter('output_cmd_vel_topic', '/cmd_vel')
        self.declare_parameter('status_topic', '/depth_camera/obstacle_status')
        self.declare_parameter('dry_run', True)
        self.declare_parameter('allow_bypass', False)
        self.declare_parameter('use_fused_pose_bypass', False)
        self.declare_parameter('danger_distance_m', 0.55)
        self.declare_parameter('clear_distance_m', 0.80)
        self.declare_parameter('min_depth_m', 0.15)
        self.declare_parameter('max_depth_m', 5.0)
        self.declare_parameter('distance_percentile', 10.0)
        self.declare_parameter('front_invalid_depth_block_fraction', 0.35)
        self.declare_parameter('side_invalid_depth_block_fraction', 0.90)
        self.declare_parameter('invalid_depth_block_fraction', -1.0)
        self.declare_parameter('publish_period_s', 0.10)
        self.declare_parameter('depth_timeout_s', 0.50)
        self.declare_parameter('cmd_timeout_s', 0.50)
        self.declare_parameter('odom_timeout_s', 0.50)
        self.declare_parameter('depth_process_period_s', 0.10)
        self.declare_parameter('bypass_angular_z', 0.25)
        self.declare_parameter('return_angular_z', 0.25)
        self.declare_parameter('return_heading_angular_sign', -1.0)
        self.declare_parameter('max_forward_mps', 0.40)
        self.declare_parameter('normal_forward_mps', 0.40)
        self.declare_parameter('bypass_forward_mps', 0.20)
        self.declare_parameter('bypass_forward_distance_m', 0.50)
        self.declare_parameter('return_heading_tolerance_rad', 0.08)
        self.declare_parameter('line_return_tolerance_m', 0.08)
        self.declare_parameter('reacquire_forward_mps', 0.10)
        self.declare_parameter('reacquire_angular_z', 0.20)
        self.declare_parameter('reacquire_max_yaw_rad', math.pi / 2.0)
        self.declare_parameter('avoid_min_forward_s', 1.0)
        self.declare_parameter('side_clear_hold_s', 1.0)
        self.declare_parameter('exit_forward_hold_s', 0.5)
        self.declare_parameter('pose_stall_timeout_s', 1.0)
        self.declare_parameter('pose_stall_distance_epsilon_m', 0.02)
        self.declare_parameter('pose_stall_yaw_epsilon_rad', 0.03)

        self._bridge = CvBridge()
        self._latest_status: Optional[ObstacleStatus] = None
        self._latest_depth_s: Optional[float] = None
        self._latest_pose: Optional[ObstaclePose] = None
        self._latest_pose_s: Optional[float] = None
        self._last_depth_process_s = 0.0
        self._latest_cmd: Optional[Twist] = None
        self._latest_cmd_s: Optional[float] = None
        self._takeover_cmd: Optional[Twist] = None
        self._bypass_controller = CompleteBypassController()
        self._last_reason = 'no_cmd'

        depth_topic = str(self.get_parameter('depth_topic').value)
        odom_topic = str(self.get_parameter('odom_topic').value)
        input_topic = str(self.get_parameter('input_cmd_vel_topic').value)
        output_topic = str(self.get_parameter('output_cmd_vel_topic').value)
        status_topic = str(self.get_parameter('status_topic').value)

        self.create_subscription(Image, depth_topic, self._on_depth, 10)
        self.create_subscription(Odometry, odom_topic, self._on_odom, 10)
        self.create_subscription(Twist, input_topic, self._on_cmd, 10)
        self._cmd_pub = self.create_publisher(Twist, output_topic, 10)
        self._status_pub = self.create_publisher(String, status_topic, 10)
        self.create_timer(float(self.get_parameter('publish_period_s').value), self._tick)

        self.get_logger().info(
            'depth obstacle guard depth=%s odom=%s input=%s output=%s dry_run=%s allow_bypass=%s fused_pose_bypass=%s'
            % (
                depth_topic,
                odom_topic,
                input_topic,
                output_topic,
                bool(self.get_parameter('dry_run').value),
                bool(self.get_parameter('allow_bypass').value),
                bool(self.get_parameter('use_fused_pose_bypass').value),
            )
        )

    def _config(self) -> ObstacleConfig:
        legacy_invalid_fraction = float(self.get_parameter('invalid_depth_block_fraction').value)
        return ObstacleConfig(
            danger_distance_m=float(self.get_parameter('danger_distance_m').value),
            clear_distance_m=float(self.get_parameter('clear_distance_m').value),
            min_depth_m=float(self.get_parameter('min_depth_m').value),
            max_depth_m=float(self.get_parameter('max_depth_m').value),
            distance_percentile=float(self.get_parameter('distance_percentile').value),
            front_invalid_depth_block_fraction=float(
                self.get_parameter('front_invalid_depth_block_fraction').value
            ),
            side_invalid_depth_block_fraction=float(
                self.get_parameter('side_invalid_depth_block_fraction').value
            ),
            invalid_depth_block_fraction=(
                legacy_invalid_fraction if legacy_invalid_fraction >= 0.0 else None
            ),
        )

    def _bypass_config(self) -> CompleteBypassConfig:
        return CompleteBypassConfig(
            normal_forward_mps=float(self.get_parameter('normal_forward_mps').value),
            bypass_forward_mps=float(self.get_parameter('bypass_forward_mps').value),
            bypass_angular_z=float(self.get_parameter('bypass_angular_z').value),
            return_angular_z=float(self.get_parameter('return_angular_z').value),
            return_heading_angular_sign=float(
                self.get_parameter('return_heading_angular_sign').value
            ),
            bypass_forward_distance_m=float(self.get_parameter('bypass_forward_distance_m').value),
            return_heading_tolerance_rad=float(self.get_parameter('return_heading_tolerance_rad').value),
            line_return_tolerance_m=float(self.get_parameter('line_return_tolerance_m').value),
            reacquire_forward_mps=float(self.get_parameter('reacquire_forward_mps').value),
            reacquire_angular_z=float(self.get_parameter('reacquire_angular_z').value),
            reacquire_max_yaw_rad=float(self.get_parameter('reacquire_max_yaw_rad').value),
            avoid_min_forward_s=float(self.get_parameter('avoid_min_forward_s').value),
            side_clear_hold_s=float(self.get_parameter('side_clear_hold_s').value),
            exit_forward_hold_s=float(self.get_parameter('exit_forward_hold_s').value),
            pose_stall_timeout_s=float(self.get_parameter('pose_stall_timeout_s').value),
            pose_stall_distance_epsilon_m=float(
                self.get_parameter('pose_stall_distance_epsilon_m').value
            ),
            pose_stall_yaw_epsilon_rad=float(
                self.get_parameter('pose_stall_yaw_epsilon_rad').value
            ),
        )

    def _on_depth(self, msg: Image) -> None:
        now = time.monotonic()
        if now - self._last_depth_process_s < float(self.get_parameter('depth_process_period_s').value):
            return
        self._last_depth_process_s = now
        depth = self._bridge.imgmsg_to_cv2(msg, desired_encoding='passthrough')
        self._latest_status = analyze_depth_zones(depth, self._config())
        self._latest_depth_s = now

    def _on_odom(self, msg: Odometry) -> None:
        pose = msg.pose.pose
        self._latest_pose = ObstaclePose(
            x=float(pose.position.x),
            y=float(pose.position.y),
            yaw=_yaw_from_quaternion(pose.orientation),
        )
        self._latest_pose_s = time.monotonic()

    def _on_cmd(self, msg: Twist) -> None:
        self._latest_cmd = msg
        self._latest_cmd_s = time.monotonic()

    def _tick(self) -> None:
        now = time.monotonic()
        output = Twist()
        reason = 'no_cmd'

        cmd_age = _age(now, self._latest_cmd_s)
        depth_age = _age(now, self._latest_depth_s)
        pose_age = _age(now, self._latest_pose_s)
        selected_cmd, using_takeover_cmd = _select_guard_input_cmd(
            latest_cmd=self._latest_cmd,
            cmd_age_s=cmd_age,
            cmd_timeout_s=float(self.get_parameter('cmd_timeout_s').value),
            bypass_phase=self._bypass_controller.state.phase,
            takeover_cmd=self._takeover_cmd,
        )

        if selected_cmd is not None:
            if self._latest_status is None:
                reason = 'no_depth'
            elif depth_age > float(self.get_parameter('depth_timeout_s').value):
                reason = 'depth_stale'
            else:
                if bool(self.get_parameter('use_fused_pose_bypass').value):
                    self._bypass_controller.config = self._bypass_config()
                    pose = self._latest_pose
                    if pose_age is None or pose_age > float(self.get_parameter('odom_timeout_s').value):
                        pose = None
                    if _should_release_bypass_to_fresh_cmd(
                        using_takeover_cmd=using_takeover_cmd,
                        bypass_phase=self._bypass_controller.state.phase,
                        last_reason=self._last_reason,
                        status=self._latest_status,
                    ):
                        self._bypass_controller.reset()
                        self._takeover_cmd = None
                    if not using_takeover_cmd:
                        self._takeover_cmd = _make_takeover_cmd(selected_cmd)
                    output, reason = self._bypass_controller.filter_velocity(
                        selected_cmd,
                        self._latest_status,
                        pose,
                        now_s=now,
                    )
                    if self._bypass_controller.state.phase == 'cruise':
                        self._takeover_cmd = None
                else:
                    self._bypass_controller.reset()
                    self._takeover_cmd = None
                    output = guard_velocity(
                        selected_cmd,
                        self._latest_status,
                        allow_bypass=bool(self.get_parameter('allow_bypass').value),
                        bypass_angular_z=float(self.get_parameter('bypass_angular_z').value),
                        max_forward_mps=float(self.get_parameter('max_forward_mps').value),
                    )
                    reason = self._latest_status.state
        elif self._latest_cmd is not None:
            reason = 'cmd_stale'
            self._bypass_controller.reset()
            self._takeover_cmd = None
        else:
            self._bypass_controller.reset()
            self._takeover_cmd = None

        dry_run = bool(self.get_parameter('dry_run').value)
        if not dry_run:
            self._cmd_pub.publish(output)

        status_msg = String()
        status_msg.data = _status_json(
            status=self._latest_status,
            output=output,
            dry_run=dry_run,
            reason=reason,
            cmd_age_s=cmd_age,
            depth_age_s=depth_age,
            pose_age_s=pose_age,
            bypass_state=self._bypass_controller.state,
            takeover_active=using_takeover_cmd,
        )
        self._status_pub.publish(status_msg)
        self._last_reason = reason


def _age(now: float, timestamp: Optional[float]) -> Optional[float]:
    if timestamp is None:
        return None
    return max(0.0, now - timestamp)


def _bypass_phase_active(phase: Optional[str]) -> bool:
    if phase is None:
        return False
    return str(phase) not in {'', 'cruise', 'idle', 'none', 'None'}


def _status_all_zones_clear(status: Optional[ObstacleStatus]) -> bool:
    if status is None:
        return False
    return (not status.front_blocked) and status.left_clear and status.right_clear


def _should_release_bypass_to_fresh_cmd(
    *,
    using_takeover_cmd: bool,
    bypass_phase: Optional[str],
    last_reason: str,
    status: Optional[ObstacleStatus],
) -> bool:
    if using_takeover_cmd:
        return False
    if not _bypass_phase_active(bypass_phase):
        return False
    if str(last_reason) not in RELEASABLE_BYPASS_REASONS:
        return False
    return _status_all_zones_clear(status)


def _make_takeover_cmd(cmd: Twist) -> Twist:
    takeover = Twist()
    takeover.linear.x = max(0.0, float(cmd.linear.x))
    takeover.angular.z = 0.0
    return takeover


def _select_guard_input_cmd(
    *,
    latest_cmd: Optional[Twist],
    cmd_age_s: Optional[float],
    cmd_timeout_s: float,
    bypass_phase: Optional[str],
    takeover_cmd: Optional[Twist],
) -> tuple[Optional[Twist], bool]:
    if latest_cmd is not None and cmd_age_s is not None and cmd_age_s <= cmd_timeout_s:
        return latest_cmd, False
    if _bypass_phase_active(bypass_phase) and takeover_cmd is not None:
        return takeover_cmd, True
    return None, False


def _status_json(
    *,
    status: Optional[ObstacleStatus],
    output: Twist,
    dry_run: bool,
    reason: str,
    cmd_age_s: Optional[float],
    depth_age_s: Optional[float],
    pose_age_s: Optional[float] = None,
    bypass_state: Optional[CompleteBypassState] = None,
    takeover_active: bool = False,
) -> str:
    front_distance_m = None if status is None else status.front_distance_m
    left_distance_m = None if status is None else status.left_distance_m
    right_distance_m = None if status is None else status.right_distance_m
    payload = {
        'state': None if status is None else status.state,
        'reason': reason,
        'front_distance_m': front_distance_m,
        'left_distance_m': left_distance_m,
        'right_distance_m': right_distance_m,
        'front_blocked': front_distance_m is None or status.front_blocked,
        'left_clear': left_distance_m is not None and status.left_clear,
        'right_clear': right_distance_m is not None and status.right_clear,
        'front_valid_fraction': None if status is None else status.front_valid_fraction,
        'front_invalid_fraction': None if status is None else status.front_invalid_fraction,
        'left_valid_fraction': None if status is None else status.left_valid_fraction,
        'left_invalid_fraction': None if status is None else status.left_invalid_fraction,
        'right_valid_fraction': None if status is None else status.right_valid_fraction,
        'right_invalid_fraction': None if status is None else status.right_invalid_fraction,
        'dry_run': dry_run,
        'output_linear_x': output.linear.x,
        'output_angular_z': output.angular.z,
        'cmd_age_s': cmd_age_s,
        'depth_age_s': depth_age_s,
        'pose_age_s': pose_age_s,
        'takeover_active': takeover_active,
        'avoidance_phase': None if bypass_state is None else bypass_state.phase,
        'target_yaw_rad': None if bypass_state is None else bypass_state.target_yaw,
        'yaw_error_rad': None if bypass_state is None else bypass_state.yaw_error_rad,
        'bypass_distance_m': None if bypass_state is None else bypass_state.pass_distance_m,
        'tracked_side': None if bypass_state is None else bypass_state.tracked_side,
        'forward_offset_m': None if bypass_state is None else bypass_state.forward_offset_m,
        'lateral_offset_m': None if bypass_state is None else bypass_state.lateral_offset_m,
        'heading_error_rad': None if bypass_state is None else bypass_state.heading_error_rad,
        'reacquire_turn_rad': None if bypass_state is None else bypass_state.reacquire_turn_rad,
        'pose_stalled': False if bypass_state is None else bypass_state.pose_stalled,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _yaw_from_quaternion(quat) -> float:
    siny_cosp = 2.0 * (quat.w * quat.z + quat.x * quat.y)
    cosy_cosp = 1.0 - 2.0 * (quat.y * quat.y + quat.z * quat.z)
    return math.atan2(siny_cosp, cosy_cosp)


def main() -> None:
    rclpy.init()
    node = ObstacleGuardNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
