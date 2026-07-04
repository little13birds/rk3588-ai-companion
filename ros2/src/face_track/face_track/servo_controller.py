#!/usr/bin/env python3
"""视觉伺服: 人脸误差 → PD 关节增量 → /joint_states → roarm_driver → 机械臂"""

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, Float32MultiArray, Int8
from sensor_msgs.msg import JointState

from face_track.arm_agent_core import BaseSweepSearch, InitialPoseController


SEARCH_IDLE = 0
SEARCH_ACTIVE = 1
SEARCH_COMPLETE = 2
PREPARE_IDLE = 0
PREPARE_ACTIVE = 1
PREPARE_COMPLETE = 2

STATE_IDLE = "idle"
STATE_STARTUP_SEARCH = "startup_search"
STATE_COARSE_ALIGN = "coarse_align"
STATE_FINE_ALIGN = "fine_align"
STATE_READY = "ready"
STATE_NEXT_PAGE_WAIT = "next_page_wait"
STATE_NEXT_PAGE_FINE_ALIGN = "next_page_fine_align"
STATE_NEXT_PAGE_LOCAL_SEARCH = "next_page_local_search"
STATE_EXIT_RETURN_HOME = "exit_return_home"


def clamp(value, min_value, max_value):
    return max(min_value, min(max_value, value))


def apply_joint_jog(current, deltas, limits):
    return [
        clamp(float(position) + float(delta), float(min_value), float(max_value))
        for position, delta, (min_value, max_value) in zip(current, deltas, limits)
    ]


def clamp_delta(value, max_abs_value):
    return clamp(value, -max_abs_value, max_abs_value)


def compute_visual_servo_deltas(e_x, e_y, e_r, de_x, de_y, de_r, gains):
    d1 = gains["kp_base"] * e_x + gains["kd_base"] * de_x
    d2 = gains["kp_shoulder"] * e_r + gains["kd_shoulder"] * de_r
    d3 = gains["kp_elbow"] * e_r + gains["kd_elbow"] * de_r
    d4 = gains["kp_wrist"] * e_y + gains["kd_wrist"] * de_y

    return [
        clamp_delta(d1, gains["max_delta_base"]),
        clamp_delta(d2, gains["max_delta_shoulder"]),
        clamp_delta(d3, gains["max_delta_elbow"]),
        clamp_delta(d4, gains["max_delta_wrist"]),
    ]


def apply_direction_jog(current, direction, error_step, gains, limits):
    e_x = float(direction[0]) * float(error_step)
    e_r = -float(direction[1]) * float(error_step)
    e_y = -float(direction[2]) * float(error_step)
    deltas = compute_visual_servo_deltas(
        e_x=e_x,
        e_y=e_y,
        e_r=e_r,
        de_x=0.0,
        de_y=0.0,
        de_r=0.0,
        gains=gains,
    )
    next_j1 = clamp(float(current[0]) + deltas[0], limits[0][0], limits[0][1])
    proposed_j2 = float(current[1]) + deltas[1]
    proposed_j3 = float(current[2]) + deltas[2]
    if (
        limits[1][0] <= proposed_j2 <= limits[1][1]
        and limits[2][0] <= proposed_j3 <= limits[2][1]
    ):
        next_j2 = proposed_j2
        next_j3 = proposed_j3
    else:
        next_j2 = float(current[1])
        next_j3 = float(current[2])
    next_j4 = clamp(float(current[3]) + deltas[3], limits[3][0], limits[3][1])
    return [next_j1, next_j2, next_j3, next_j4]


def move_toward(current, target, max_delta):
    current = float(current)
    target = float(target)
    max_delta = abs(float(max_delta))
    diff = target - current
    if abs(diff) <= max_delta:
        return target
    return current + (max_delta if diff > 0 else -max_delta)


def move_joints_toward(current, target, max_delta, limits, tolerance=0.002):
    next_pos = []
    done = True
    for value, target_value, step, (min_value, max_value) in zip(
        current, target, max_delta, limits
    ):
        value = float(value)
        target_value = clamp(float(target_value), min_value, max_value)
        if abs(target_value - value) <= tolerance:
            next_value = target_value
        else:
            done = False
            next_value = move_toward(value, target_value, step)
        next_pos.append(round(clamp(next_value, min_value, max_value), 6))
    done = all(abs(a - b) <= tolerance for a, b in zip(next_pos, target))
    return next_pos, done


def physical_j3_search_levels(j3_min, search_j3_mid, j3_max):
    """Return physical down/mid/up levels for j3.

    On this arm, the smaller ROS command value is the physical upper j3 limit.
    """
    return [float(j3_max), float(search_j3_mid), float(j3_min)]


def return_home_pose(j3_physical_lower):
    return [0.0, 0.0, float(j3_physical_lower), 0.0]


def scale_joint_deltas(max_delta, multiplier):
    multiplier = float(multiplier)
    if multiplier <= 0:
        raise ValueError("multiplier must be positive")
    return [round(float(value) * multiplier, 6) for value in max_delta]


class CoarseAlignmentGate:
    def __init__(self, threshold_x, threshold_y, stable_sec):
        self.threshold_x = float(threshold_x)
        self.threshold_y = float(threshold_y)
        self.stable_sec = float(stable_sec)
        self._stable_time = 0.0

    def reset(self):
        self._stable_time = 0.0

    def update(self, e_x, e_y, dt):
        if abs(float(e_x)) <= self.threshold_x and abs(float(e_y)) <= self.threshold_y:
            self._stable_time += max(0.0, float(dt))
        else:
            self._stable_time = 0.0
        return self._stable_time >= self.stable_sec


class LostBookGate:
    def __init__(self, grace_sec):
        self.grace_sec = max(0.0, float(grace_sec))
        self._lost_time = 0.0

    def reset(self):
        self._lost_time = 0.0

    def update(self, found, dt):
        if found:
            self.reset()
            return False
        self._lost_time += max(0.0, float(dt))
        return self._lost_time >= self.grace_sec


class StableDetectionGate:
    def __init__(self, stable_sec):
        self.stable_sec = max(0.0, float(stable_sec))
        self.stable_time = 0.0

    def reset(self):
        self.stable_time = 0.0

    def update(self, found, dt):
        if not found:
            self.reset()
            return False
        self.stable_time += max(0.0, float(dt))
        return self.stable_time >= self.stable_sec


def recovery_state_for_confirmed_loss(state):
    if state in (STATE_FINE_ALIGN, STATE_NEXT_PAGE_FINE_ALIGN, STATE_READY):
        return STATE_NEXT_PAGE_LOCAL_SEARCH
    if state == STATE_EXIT_RETURN_HOME:
        return STATE_EXIT_RETURN_HOME
    return STATE_STARTUP_SEARCH


def alignment_state_for_found_book(state, raw_e_x, raw_e_y, raw_e_r, deadbands):
    if state != STATE_READY:
        return state
    deadband_x, deadband_y, deadband_r = [float(value) for value in deadbands]
    if (
        abs(float(raw_e_x)) >= deadband_x
        or abs(float(raw_e_y)) >= deadband_y
        or abs(float(raw_e_r)) >= deadband_r
    ):
        return STATE_FINE_ALIGN
    return state


def should_publish_hold_command(state, preparing, tracking, hold_enabled=True):
    if not hold_enabled:
        return False
    if preparing:
        return True
    return state in (
        STATE_STARTUP_SEARCH,
        STATE_COARSE_ALIGN,
        STATE_FINE_ALIGN,
        STATE_READY,
        STATE_NEXT_PAGE_FINE_ALIGN,
        STATE_NEXT_PAGE_LOCAL_SEARCH,
        STATE_EXIT_RETURN_HOME,
    )


def control_publish_period(hz):
    hz = float(hz)
    if hz <= 0.0:
        raise ValueError("control_publish_hz must be positive")
    return 1.0 / hz


def classify_visual_freshness(age_sec, stale_hold_sec, lost_sec):
    stale_hold_sec = float(stale_hold_sec)
    lost_sec = float(lost_sec)
    if stale_hold_sec < 0.0 or lost_sec < 0.0:
        raise ValueError("visual freshness thresholds must be non-negative")
    if stale_hold_sec > lost_sec:
        raise ValueError("vision_stale_hold_sec must be <= vision_lost_sec")
    age_sec = max(0.0, float(age_sec))
    if age_sec <= stale_hold_sec:
        return "fresh"
    if age_sec <= lost_sec:
        return "hold"
    return "lost"


def valid_book_detection(found, area_ratio, min_area_ratio):
    return float(found) >= 0.5 and float(area_ratio) >= float(min_area_ratio)


def apply_coarse_alignment(
    current,
    e_x,
    e_y,
    de_x,
    de_y,
    gains,
    limits,
    hold_j2=0.0,
    hold_j4=0.0,
):
    d1 = gains["kp_base"] * e_x + gains["kd_base"] * de_x
    d3 = gains["kp_elbow"] * e_y + gains["kd_elbow"] * de_y
    d1 = clamp_delta(d1, gains["max_delta_base"])
    d3 = clamp_delta(d3, gains["max_delta_elbow"])

    next_pos = [
        clamp(float(current[0]) + d1, limits[0][0], limits[0][1]),
        clamp(float(hold_j2), limits[1][0], limits[1][1]),
        clamp(float(current[2]) + d3, limits[2][0], limits[2][1]),
        clamp(float(hold_j4), limits[3][0], limits[3][1]),
    ]
    return next_pos, [d1, 0.0, d3, 0.0]


def apply_fine_alignment(
    current,
    e_x,
    e_y,
    e_r,
    de_x,
    de_y,
    de_r,
    gains,
    fine_base_gains,
    limits,
):
    if abs(e_x) < fine_base_gains["deadband_x"]:
        e_x = 0.0
        de_x = 0.0

    d1 = fine_base_gains["kp_base"] * e_x + fine_base_gains["kd_base"] * de_x
    d1 = clamp_delta(d1, fine_base_gains["max_delta_base"])
    d2 = gains["kp_shoulder"] * e_r + gains["kd_shoulder"] * de_r
    d3 = gains["kp_elbow"] * e_r + gains["kd_elbow"] * de_r
    wrist_error_sign = float(gains.get("fine_wrist_error_sign", 1.0))
    wrist_e_y = wrist_error_sign * e_y
    wrist_de_y = wrist_error_sign * de_y
    d4 = gains["kp_wrist"] * wrist_e_y + gains["kd_wrist"] * wrist_de_y
    d2 = clamp_delta(d2, gains["max_delta_shoulder"])
    d3 = clamp_delta(d3, gains["max_delta_elbow"])
    d4 = clamp_delta(d4, gains["max_delta_wrist"])

    next_j1 = clamp(float(current[0]) + d1, limits[0][0], limits[0][1])
    proposed_j2 = float(current[1]) + d2
    proposed_j3 = float(current[2]) + d3
    if (
        limits[1][0] <= proposed_j2 <= limits[1][1]
        and limits[2][0] <= proposed_j3 <= limits[2][1]
    ):
        next_j2 = proposed_j2
        next_j3 = proposed_j3
    else:
        next_j2 = float(current[1])
        next_j3 = float(current[2])
        d2 = 0.0
        d3 = 0.0
    next_j4 = clamp(float(current[3]) + d4, limits[3][0], limits[3][1])
    return [next_j1, next_j2, next_j3, next_j4], [d1, d2, d3, d4]


class StartupBookSearch:
    def __init__(
        self,
        j3_levels,
        search_min,
        search_max,
        search_step,
        max_delta,
        limits,
        hold_j2=0.0,
        hold_j4=0.0,
        tolerance=0.002,
    ):
        if not j3_levels:
            raise ValueError("j3_levels must not be empty")
        self.j3_levels = [float(value) for value in j3_levels]
        self.max_delta = [float(value) for value in max_delta]
        self.limits = limits
        self.hold_j2 = float(hold_j2)
        self.hold_j4 = float(hold_j4)
        self.tolerance = float(tolerance)
        self.base_search = BaseSweepSearch(search_min, search_max, search_step)
        self.active = False
        self._level_index = 0
        self._moving_to_level = True

    def start(self):
        self.active = True
        self._level_index = 0
        self._moving_to_level = True
        self.base_search.stop()

    def stop(self):
        self.active = False
        self.base_search.stop()

    def snapshot(self):
        target_j3 = None
        if self._level_index < len(self.j3_levels):
            target_j3 = self.j3_levels[self._level_index]
        return {
            "active": self.active,
            "level_index": self._level_index,
            "level_count": len(self.j3_levels),
            "target_j3": target_j3,
            "phase": "move_j3" if self._moving_to_level else "sweep_j1",
            "sweep_active": self.base_search.active,
        }

    def advance(self, current):
        if not self.active:
            self.start()

        current = [float(value) for value in current]
        if self._level_index >= len(self.j3_levels):
            self.stop()
            return current, True

        level = self.j3_levels[self._level_index]
        if self._moving_to_level:
            target = [current[0], self.hold_j2, level, self.hold_j4]
            next_pos, done = move_joints_toward(
                current=current,
                target=target,
                max_delta=self.max_delta,
                limits=self.limits,
                tolerance=self.tolerance,
            )
            if done:
                self._moving_to_level = False
                self.base_search.start(next_pos[0])
            return next_pos, False

        next_j1, sweep_done = self.base_search.advance(current[0])
        next_pos = [
            clamp(next_j1, self.limits[0][0], self.limits[0][1]),
            clamp(self.hold_j2, self.limits[1][0], self.limits[1][1]),
            clamp(level, self.limits[2][0], self.limits[2][1]),
            clamp(self.hold_j4, self.limits[3][0], self.limits[3][1]),
        ]
        if sweep_done:
            self._level_index += 1
            self._moving_to_level = True
            self.base_search.stop()
            if self._level_index >= len(self.j3_levels):
                self.stop()
                return next_pos, True
        return next_pos, False


class NextPageLocalSearch:
    def __init__(self, center, radii, step, limits, tolerance=0.002):
        if not radii:
            raise ValueError("radii must not be empty")
        if step <= 0:
            raise ValueError("step must be positive")
        self.center = [float(value) for value in center]
        self.radii = [float(value) for value in radii]
        self.step = float(step)
        self.limits = limits
        self.tolerance = float(tolerance)
        self.active = False
        self._targets = []
        self._target_index = 0

    def start(self):
        self._targets = []
        c1, c2, c3, c4 = self.center
        for radius in self.radii:
            self._targets.extend([
                [c1 + radius, c2, c3, c4],
                [c1 - radius, c2, c3, c4],
                [c1, c2, c3, c4],
                [c1, c2, c3, c4 + radius],
                [c1, c2, c3, c4 - radius],
                [c1, c2, c3, c4],
            ])
        self._targets = [
            [
                clamp(target[0], self.limits[0][0], self.limits[0][1]),
                self.center[1],
                self.center[2],
                clamp(target[3], self.limits[3][0], self.limits[3][1]),
            ]
            for target in self._targets
        ]
        self._target_index = 0
        self.active = True

    def stop(self):
        self.active = False
        self._targets = []
        self._target_index = 0

    def advance(self, current):
        if not self.active:
            self.start()
        if self._target_index >= len(self._targets):
            self.stop()
            return list(current), True

        target = self._targets[self._target_index]
        target = [target[0], self.center[1], self.center[2], target[3]]
        next_pos, reached = move_joints_toward(
            current=[current[0], self.center[1], self.center[2], current[3]],
            target=target,
            max_delta=[self.step, self.step, self.step, self.step],
            limits=self.limits,
            tolerance=self.tolerance,
        )
        if reached:
            self._target_index += 1
            if self._target_index >= len(self._targets):
                self.stop()
                return next_pos, True
        return next_pos, False


class ServoController(Node):
    def __init__(self):
        super().__init__('servo_controller')

        self.declare_parameter('target_x', 0.5)
        self.declare_parameter('target_y', 0.5)
        self.declare_parameter('target_ratio', 0.20)

        self.declare_parameter('kp_base', 8.0)
        self.declare_parameter('kp_shoulder', 4.0)
        self.declare_parameter('kp_elbow', 5.0)
        self.declare_parameter('kp_wrist', 5.0)
        self.declare_parameter('kd_base', 1.5)
        self.declare_parameter('kd_shoulder', 0.8)
        self.declare_parameter('kd_elbow', 1.0)
        self.declare_parameter('kd_wrist', 1.0)

        self.declare_parameter('max_delta_base', 0.1047)     # 60°/s @10Hz
        self.declare_parameter('max_delta_shoulder', 0.0524) # 30°/s @10Hz
        self.declare_parameter('max_delta_elbow', 0.0524)    # 30°/s @10Hz
        self.declare_parameter('max_delta_wrist', 0.0524)    # 30°/s @10Hz
        self.declare_parameter('initial_pose_speed_multiplier', 2.0)

        self.declare_parameter('deadband_x', 0.01)
        self.declare_parameter('deadband_y', 0.01)
        self.declare_parameter('deadband_r', 0.01)

        self.declare_parameter('j1_min', -3.14)
        self.declare_parameter('j1_max', 3.14)
        self.declare_parameter('j2_min', -1.047)
        self.declare_parameter('j2_max', 0.0)
        self.declare_parameter('j3_min', -1.0)
        self.declare_parameter('j3_max', 3.14)
        self.declare_parameter('j4_min', -1.047)
        self.declare_parameter('j4_max', 1.047)

        self.declare_parameter('search_min', -1.57)
        self.declare_parameter('search_max', 1.57)
        self.declare_parameter('search_step', 0.04)
        self.declare_parameter('search_period', 0.10)
        self.declare_parameter('control_publish_hz', 20.0)
        self.declare_parameter('hold_publish_when_active', True)
        self.declare_parameter('vision_stale_hold_sec', 0.3)
        self.declare_parameter('vision_lost_sec', 0.8)
        self.declare_parameter('direction_error_step', 0.2)
        self.declare_parameter('search_j3_min', 0.785)
        self.declare_parameter('search_j3_mid', 1.571)
        self.declare_parameter('search_j3_max', 2.356)
        self.declare_parameter('startup_found_stable_sec', 0.3)
        self.declare_parameter('min_book_area_ratio', 0.02)
        self.declare_parameter('coarse_deadband_x', 0.12)
        self.declare_parameter('coarse_deadband_y', 0.12)
        self.declare_parameter('coarse_stable_sec', 0.3)
        self.declare_parameter('kp_base_fine', 0.08)
        self.declare_parameter('kd_base_fine', 0.03)
        self.declare_parameter('max_delta_base_fine', 0.012)
        self.declare_parameter('fine_deadband_x', 0.16)
        self.declare_parameter('fine_wrist_error_sign', -1.0)
        self.declare_parameter('next_page_search_radius_1', 0.524)
        self.declare_parameter('next_page_search_radius_2', 0.785)
        self.declare_parameter('lost_book_grace_sec', 0.5)

        self.declare_parameter('initial_j1', 0.0)
        self.declare_parameter('initial_j2', 0.0)
        self.declare_parameter('initial_j3', 2.618)
        self.declare_parameter('initial_j4', 0.0)
        self.declare_parameter('prepare_tolerance', 0.003)

        self.j1 = self.get_parameter('initial_j1').value
        self.j2 = self.get_parameter('initial_j2').value
        self.j3 = self.get_parameter('initial_j3').value
        self.j4 = self.get_parameter('initial_j4').value

        self.prev_e_x = 0.0
        self.prev_e_y = 0.0
        self.prev_e_r = 0.0
        self.coarse_stable_time = 0.0
        self.last_time = self.get_clock().now()
        self.tracking = False
        self.search_requested = False
        self.search_complete = False
        self.search_status = SEARCH_IDLE
        self.preparing = False
        self.prepare_status = PREPARE_IDLE
        self.state = STATE_IDLE
        self.next_page_pose = None
        self.last_published_joints = None
        self.last_face_info_time = None
        self.initial_pose = [
            self.get_parameter('initial_j1').value,
            self.get_parameter('initial_j2').value,
            self.get_parameter('initial_j3').value,
            self.get_parameter('initial_j4').value,
        ]
        self.prepare_motion = self._new_prepare_motion()
        self.return_home_motion = self._new_return_home_motion()
        self.coarse_gate = self._new_coarse_gate()
        self.lost_book_gate = self._new_lost_book_gate()
        self.startup_found_gate = self._new_startup_found_gate()
        self.startup_search = self._new_startup_search()
        self.next_page_search = None
        self.search = BaseSweepSearch(
            min_pos=self.get_parameter('search_min').value,
            max_pos=self.get_parameter('search_max').value,
            step=self.get_parameter('search_step').value,
        )

        self.face_info_sub = self.create_subscription(
            Float32MultiArray, '/face_info', self.face_info_callback, 10
        )
        self.joint_jog_sub = self.create_subscription(
            Float32MultiArray, '/joint_jog', self.joint_jog_callback, 10
        )
        self.direction_jog_sub = self.create_subscription(
            Float32MultiArray, '/direction_jog', self.direction_jog_callback, 10
        )
        self.prepare_sub = self.create_subscription(
            Bool, '/book_prepare', self.prepare_callback, 10
        )
        self.return_home_sub = self.create_subscription(
            Bool, '/book_return_home', self.return_home_callback, 10
        )
        self.joint_pub = self.create_publisher(JointState, '/joint_states', 10)
        self.search_status_pub = self.create_publisher(
            Int8, '/book_search_status', 10
        )
        self.prepare_status_pub = self.create_publisher(
            Int8, '/book_prepare_status', 10
        )
        self.search_timer = self.create_timer(
            self.get_parameter('search_period').value,
            self.search_tick,
        )
        self.control_timer = self.create_timer(
            control_publish_period(self.get_parameter('control_publish_hz').value),
            self.control_tick,
        )

        self.get_logger().info(
            f'ServoController ready (staged). Init J=({self.j1:.3f},{self.j2:.3f},'
            f'{self.j3:.3f},{self.j4:.3f})'
        )

    def prepare_callback(self, msg: Bool):
        if not msg.data:
            self.preparing = False
            self._set_prepare_status(PREPARE_IDLE)
            return
        self.tracking = False
        self.search_requested = False
        self.search_complete = False
        self._stop_motion_searches()
        self._set_search_status(SEARCH_IDLE)
        self._reset_errors()
        self.state = STATE_IDLE
        self.preparing = True
        self.prepare_motion = self._new_prepare_motion()
        self._set_prepare_status(PREPARE_ACTIVE)
        self.get_logger().info(
            f'Reading arm prepare started: target J=({self.initial_pose[0]:.3f},'
            f'{self.initial_pose[1]:.3f},{self.initial_pose[2]:.3f},{self.initial_pose[3]:.3f}) '
            f'speed_multiplier={self.get_parameter("initial_pose_speed_multiplier").value:.3f}'
        )

    def return_home_callback(self, msg: Bool):
        if not msg.data:
            return
        self.tracking = False
        self.search_requested = False
        self.search_complete = False
        self.preparing = False
        self._stop_motion_searches()
        self._set_search_status(SEARCH_IDLE)
        self._set_prepare_status(PREPARE_IDLE)
        self._reset_errors()
        self.return_home_motion = self._new_return_home_motion()
        self.state = STATE_EXIT_RETURN_HOME
        self.get_logger().info(
            f'Reading arm return-home started: target J=(0.000,0.000,'
            f'{self.get_parameter("j3_max").value:.3f},0.000)'
        )

    def _cancel_active_modes_for_manual_control(self):
        self.tracking = False
        self.search_requested = False
        self.search_complete = False
        self.preparing = False
        self._stop_motion_searches()
        self._set_search_status(SEARCH_IDLE)
        self._set_prepare_status(PREPARE_IDLE)
        self._reset_errors()
        self.state = STATE_IDLE

    def _joint_limits(self):
        return [
            (self.get_parameter('j1_min').value, self.get_parameter('j1_max').value),
            (self.get_parameter('j2_min').value, self.get_parameter('j2_max').value),
            (self.get_parameter('j3_min').value, self.get_parameter('j3_max').value),
            (self.get_parameter('j4_min').value, self.get_parameter('j4_max').value),
        ]

    def _max_delta(self):
        return [
            self.get_parameter('max_delta_base').value,
            self.get_parameter('max_delta_shoulder').value,
            self.get_parameter('max_delta_elbow').value,
            self.get_parameter('max_delta_wrist').value,
        ]

    def _initial_pose_max_delta(self):
        return scale_joint_deltas(
            self._max_delta(),
            self.get_parameter('initial_pose_speed_multiplier').value,
        )

    def _servo_gains(self):
        return {
            "kp_base": self.get_parameter('kp_base').value,
            "kd_base": self.get_parameter('kd_base').value,
            "kp_shoulder": self.get_parameter('kp_shoulder').value,
            "kd_shoulder": self.get_parameter('kd_shoulder').value,
            "kp_elbow": self.get_parameter('kp_elbow').value,
            "kd_elbow": self.get_parameter('kd_elbow').value,
            "kp_wrist": self.get_parameter('kp_wrist').value,
            "kd_wrist": self.get_parameter('kd_wrist').value,
            "fine_wrist_error_sign": self.get_parameter('fine_wrist_error_sign').value,
            "max_delta_base": self.get_parameter('max_delta_base').value,
            "max_delta_shoulder": self.get_parameter('max_delta_shoulder').value,
            "max_delta_elbow": self.get_parameter('max_delta_elbow').value,
            "max_delta_wrist": self.get_parameter('max_delta_wrist').value,
        }

    def _fine_base_gains(self):
        return {
            "kp_base": self.get_parameter('kp_base_fine').value,
            "kd_base": self.get_parameter('kd_base_fine').value,
            "max_delta_base": self.get_parameter('max_delta_base_fine').value,
            "deadband_x": self.get_parameter('fine_deadband_x').value,
        }

    def _new_coarse_gate(self):
        return CoarseAlignmentGate(
            threshold_x=self.get_parameter('coarse_deadband_x').value,
            threshold_y=self.get_parameter('coarse_deadband_y').value,
            stable_sec=self.get_parameter('coarse_stable_sec').value,
        )

    def _new_lost_book_gate(self):
        return LostBookGate(max(
            self.get_parameter('lost_book_grace_sec').value,
            self.get_parameter('vision_lost_sec').value,
        ))

    def _new_startup_found_gate(self):
        return StableDetectionGate(
            self.get_parameter('startup_found_stable_sec').value
        )

    def _new_startup_search(self):
        return StartupBookSearch(
            j3_levels=physical_j3_search_levels(
                self.get_parameter('search_j3_min').value,
                self.get_parameter('search_j3_mid').value,
                self.get_parameter('search_j3_max').value,
            ),
            search_min=self.get_parameter('search_min').value,
            search_max=self.get_parameter('search_max').value,
            search_step=self.get_parameter('search_step').value,
            max_delta=self._max_delta(),
            limits=self._joint_limits(),
            hold_j2=0.0,
            hold_j4=0.0,
            tolerance=self.get_parameter('prepare_tolerance').value,
        )

    def _new_next_page_search(self, center):
        return NextPageLocalSearch(
            center=center,
            radii=[
                self.get_parameter('next_page_search_radius_1').value,
                self.get_parameter('next_page_search_radius_2').value,
            ],
            step=self.get_parameter('search_step').value,
            limits=self._joint_limits(),
            tolerance=self.get_parameter('prepare_tolerance').value,
        )

    def _new_prepare_motion(self):
        return InitialPoseController(
            target=self.initial_pose,
            max_delta=self._initial_pose_max_delta(),
            tolerance=self.get_parameter('prepare_tolerance').value,
        )

    def _new_return_home_motion(self):
        return InitialPoseController(
            target=return_home_pose(self.get_parameter('j3_max').value),
            max_delta=self._initial_pose_max_delta(),
            tolerance=self.get_parameter('prepare_tolerance').value,
        )

    def _reset_errors(self):
        self.prev_e_x = self.prev_e_y = self.prev_e_r = 0.0
        self.coarse_gate.reset()

    def _stop_motion_searches(self):
        self.search.stop()
        self.startup_search.stop()
        if self.next_page_search is not None:
            self.next_page_search.stop()

    def _enter_startup_search(self):
        self.state = STATE_STARTUP_SEARCH
        self.search_requested = True
        self.search_complete = False
        self.next_page_search = None
        self.startup_search = self._new_startup_search()
        self.startup_search.start()
        self.startup_found_gate = self._new_startup_found_gate()
        self.coarse_gate.reset()
        self._set_search_status(SEARCH_ACTIVE)
        levels = ','.join(f'{level:.3f}' for level in self.startup_search.j3_levels)
        self.get_logger().info(
            f'state=startup_search started levels=[{levels}] '
            f'j1_range=({self.get_parameter("search_min").value:.3f},'
            f'{self.get_parameter("search_max").value:.3f}) '
            f'found_stable_sec={self.startup_found_gate.stable_sec:.3f}'
        )

    def _enter_next_page_local_search(self):
        self.state = STATE_NEXT_PAGE_LOCAL_SEARCH
        self.search_requested = True
        self.search_complete = False
        self.next_page_pose = [self.j1, self.j2, self.j3, self.j4]
        self.next_page_search = self._new_next_page_search(self.next_page_pose)
        self.next_page_search.start()
        self._set_search_status(SEARCH_ACTIVE)

    def joint_jog_callback(self, msg: Float32MultiArray):
        if len(msg.data) < 4:
            self.get_logger().warning('Ignoring /joint_jog with fewer than 4 deltas')
            return

        self._cancel_active_modes_for_manual_control()

        current = [self.j1, self.j2, self.j3, self.j4]
        deltas = list(msg.data[:4])
        limits = self._joint_limits()
        self.j1, self.j2, self.j3, self.j4 = apply_joint_jog(current, deltas, limits)
        self._publish_joint_state()

        self.get_logger().info(
            f'joint_jog d=({deltas[0]:.3f},{deltas[1]:.3f},{deltas[2]:.3f},{deltas[3]:.3f}) '
            f'J=({self.j1:.3f},{self.j2:.3f},{self.j3:.3f},{self.j4:.3f})'
        )

    def direction_jog_callback(self, msg: Float32MultiArray):
        if len(msg.data) < 3:
            self.get_logger().warning('Ignoring /direction_jog with fewer than 3 values')
            return

        self._cancel_active_modes_for_manual_control()

        current = [self.j1, self.j2, self.j3, self.j4]
        direction = list(msg.data[:3])
        self.j1, self.j2, self.j3, self.j4 = apply_direction_jog(
            current=current,
            direction=direction,
            error_step=self.get_parameter('direction_error_step').value,
            gains=self._servo_gains(),
            limits=self._joint_limits(),
        )
        self._publish_joint_state()

        self.get_logger().info(
            f'direction_jog dir=({direction[0]:.0f},{direction[1]:.0f},{direction[2]:.0f}) '
            f'J=({self.j1:.3f},{self.j2:.3f},{self.j3:.3f},{self.j4:.3f})'
        )

    def face_info_callback(self, msg: Float32MultiArray):
        if len(msg.data) < 4:
            return

        callback_time = self.get_clock().now()
        self.last_face_info_time = callback_time

        face_x, face_y, face_ratio, found = msg.data[:4]
        tracking = msg.data[4] if len(msg.data) >= 5 else 0.0

        if tracking < 0.5:
            if self.tracking and self.state not in (STATE_IDLE, STATE_EXIT_RETURN_HOME):
                self.state = STATE_NEXT_PAGE_WAIT
                self.next_page_pose = [self.j1, self.j2, self.j3, self.j4]
            self.tracking = False
            self.search_requested = False
            self._stop_motion_searches()
            if self.state != STATE_EXIT_RETURN_HOME:
                self.search_complete = False
                self._set_search_status(SEARCH_IDLE)
            self._reset_errors()
            self.lost_book_gate.reset()
            return

        if not self.tracking:
            self.tracking = True
            self.search_complete = False
            self._reset_errors()
            self.lost_book_gate.reset()
            if self.state == STATE_NEXT_PAGE_WAIT:
                self.state = STATE_NEXT_PAGE_FINE_ALIGN
                self._set_search_status(SEARCH_IDLE)
            else:
                self._enter_startup_search()

        now = callback_time
        dt = (now - self.last_time).nanoseconds / 1e9
        if dt <= 0 or dt > 0.5:
            dt = 0.033  # fallback ~30Hz
        self.last_time = now

        min_book_area_ratio = self.get_parameter('min_book_area_ratio').value
        if not valid_book_detection(found, face_ratio, min_book_area_ratio):
            if self.state in (STATE_STARTUP_SEARCH, STATE_NEXT_PAGE_LOCAL_SEARCH):
                if self.state == STATE_STARTUP_SEARCH:
                    self.startup_found_gate.reset()
                self.search_requested = not self.search_complete
                if found >= 0.5:
                    self.get_logger().info(
                        f'state={self.state} ignore tiny detection '
                        f'ratio={face_ratio:.4f} min={min_book_area_ratio:.4f}'
                    )
            elif self.lost_book_gate.update(found=False, dt=dt):
                recovery_state = recovery_state_for_confirmed_loss(self.state)
                if recovery_state == STATE_NEXT_PAGE_LOCAL_SEARCH:
                    self._enter_next_page_local_search()
                elif recovery_state == STATE_STARTUP_SEARCH:
                    self._enter_startup_search()
            else:
                self.get_logger().info(
                    f'state={self.state} transient book loss ignored'
                )
            self._reset_errors()
            return

        self.lost_book_gate.reset()
        self.search_requested = False
        self.search_complete = False

        if self.state == STATE_STARTUP_SEARCH:
            if not self.startup_found_gate.update(found=True, dt=dt):
                self._set_search_status(SEARCH_ACTIVE)
                self.get_logger().info(
                    f'state=startup_search found_confirm '
                    f'stable={self.startup_found_gate.stable_time:.3f}/'
                    f'{self.startup_found_gate.stable_sec:.3f} '
                    f'cx={face_x:.3f} cy={face_y:.3f} ratio={face_ratio:.3f} '
                    f'J=({self.j1:.3f},{self.j2:.3f},{self.j3:.3f},{self.j4:.3f})'
                )
                self._reset_errors()
                return
            self._stop_motion_searches()
            self.state = STATE_COARSE_ALIGN
            self.coarse_gate.reset()
            self._set_search_status(SEARCH_ACTIVE)
            self.get_logger().info(
                f'state=startup_search found_confirmed '
                f'stable={self.startup_found_gate.stable_time:.3f}/'
                f'{self.startup_found_gate.stable_sec:.3f}; entering coarse_align'
            )
        elif self.state == STATE_NEXT_PAGE_LOCAL_SEARCH:
            self._stop_motion_searches()
            self.state = STATE_NEXT_PAGE_FINE_ALIGN
            self._set_search_status(SEARCH_IDLE)
        elif self.state == STATE_NEXT_PAGE_WAIT:
            self.state = STATE_NEXT_PAGE_FINE_ALIGN
            self._set_search_status(SEARCH_IDLE)

        raw_e_x = face_x - self.get_parameter('target_x').value
        raw_e_y = face_y - self.get_parameter('target_y').value
        raw_e_r = face_ratio - self.get_parameter('target_ratio').value
        deadband_x = self.get_parameter('deadband_x').value
        deadband_y = self.get_parameter('deadband_y').value
        deadband_r = self.get_parameter('deadband_r').value
        next_state = alignment_state_for_found_book(
            self.state,
            raw_e_x,
            raw_e_y,
            raw_e_r,
            (deadband_x, deadband_y, deadband_r),
        )
        if next_state != self.state:
            self.get_logger().info(
                f'state={self.state} realign requested '
                f'raw_e=({raw_e_x:.3f},{raw_e_y:.3f},{raw_e_r:.3f}); '
                f'entering {next_state}'
            )
            self.state = next_state
        e_x = raw_e_x
        e_y = raw_e_y
        e_r = raw_e_r

        if abs(e_x) < deadband_x: e_x = 0.0
        if abs(e_y) < deadband_y: e_y = 0.0
        if abs(e_r) < deadband_r: e_r = 0.0

        de_x = (e_x - self.prev_e_x) / dt
        de_y = (e_y - self.prev_e_y) / dt
        de_r = (e_r - self.prev_e_r) / dt
        self.prev_e_x = e_x
        self.prev_e_y = e_y
        self.prev_e_r = e_r

        if self.state == STATE_COARSE_ALIGN:
            next_pos, deltas = apply_coarse_alignment(
                current=[self.j1, self.j2, self.j3, self.j4],
                e_x=e_x,
                e_y=e_y,
                de_x=de_x,
                de_y=de_y,
                gains=self._servo_gains(),
                limits=self._joint_limits(),
            )
            self.j1, self.j2, self.j3, self.j4 = next_pos
            d1, d2, d3, d4 = deltas
            self._publish_joint_state()
            if self.coarse_gate.update(raw_e_x, raw_e_y, dt):
                self.state = STATE_FINE_ALIGN
                self._reset_errors()
                self._set_search_status(SEARCH_IDLE)
                self.get_logger().info('Coarse align complete; entering fine align')
            self.get_logger().info(
                f'state={self.state} e=({e_x:.3f},{e_y:.3f},{e_r:.3f}) '
                f'd=({d1:.3f},{d2:.3f},{d3:.3f},{d4:.3f}) '
                f'J=({self.j1:.3f},{self.j2:.3f},{self.j3:.3f},{self.j4:.3f})'
            )
            return

        if self.state in (STATE_FINE_ALIGN, STATE_NEXT_PAGE_FINE_ALIGN):
            self._set_search_status(SEARCH_IDLE)
            next_pos, deltas = apply_fine_alignment(
                current=[self.j1, self.j2, self.j3, self.j4],
                e_x=e_x,
                e_y=e_y,
                e_r=e_r,
                de_x=de_x,
                de_y=de_y,
                de_r=de_r,
                gains=self._servo_gains(),
                fine_base_gains=self._fine_base_gains(),
                limits=self._joint_limits(),
            )
            self.j1, self.j2, self.j3, self.j4 = next_pos
            d1, d2, d3, d4 = deltas
            self._publish_joint_state()
            if e_x == 0.0 and e_y == 0.0 and e_r == 0.0 and all(
                abs(delta) < 1e-9 for delta in deltas
            ):
                self.state = STATE_READY
            self.get_logger().info(
                f'state={self.state} e=({e_x:.3f},{e_y:.3f},{e_r:.3f}) '
                f'd=({d1:.3f},{d2:.3f},{d3:.3f},{d4:.3f}) '
                f'J=({self.j1:.3f},{self.j2:.3f},{self.j3:.3f},{self.j4:.3f})'
            )
            return

        if self.state == STATE_READY:
            self._set_search_status(SEARCH_IDLE)

    def control_tick(self):
        self._handle_visual_staleness()
        if not should_publish_hold_command(
            self.state,
            self.preparing,
            self.tracking,
            self.get_parameter('hold_publish_when_active').value,
        ):
            return
        self._publish_joint_state()

    def _visual_age_sec(self):
        if self.last_face_info_time is None:
            return None
        now = self.get_clock().now()
        return max(0.0, (now - self.last_face_info_time).nanoseconds / 1e9)

    def _handle_visual_staleness(self):
        if self.preparing or not self.tracking:
            return
        if self.state in (STATE_IDLE, STATE_STARTUP_SEARCH, STATE_NEXT_PAGE_LOCAL_SEARCH, STATE_EXIT_RETURN_HOME):
            return
        age = self._visual_age_sec()
        if age is None:
            return
        freshness = classify_visual_freshness(
            age,
            self.get_parameter('vision_stale_hold_sec').value,
            self.get_parameter('vision_lost_sec').value,
        )
        if freshness != "lost":
            return

        recovery_state = recovery_state_for_confirmed_loss(self.state)
        if recovery_state == STATE_NEXT_PAGE_LOCAL_SEARCH:
            self.get_logger().warning(
                f'Visual input stale for {age:.3f}s; entering next-page local search'
            )
            self._enter_next_page_local_search()
        elif recovery_state == STATE_STARTUP_SEARCH:
            self.get_logger().warning(
                f'Visual input stale for {age:.3f}s; entering startup search'
            )
            self._enter_startup_search()
        self._reset_errors()

    def search_tick(self):
        if self.preparing:
            next_pos, done = self.prepare_motion.advance([self.j1, self.j2, self.j3, self.j4])
            self.j1, self.j2, self.j3, self.j4 = next_pos
            self._publish_joint_state()
            if done:
                self.preparing = False
                self._set_prepare_status(PREPARE_COMPLETE)
                self.get_logger().info('Reading arm prepare complete')
            return

        if self.state == STATE_EXIT_RETURN_HOME:
            next_pos, done = self.return_home_motion.advance([self.j1, self.j2, self.j3, self.j4])
            self.j1, self.j2, self.j3, self.j4 = next_pos
            self._publish_joint_state()
            if done:
                self.state = STATE_IDLE
                self._set_search_status(SEARCH_IDLE)
                self.get_logger().info('Reading arm return-home complete')
            return

        if not self.tracking or not self.search_requested or self.search_complete:
            return

        if self.state == STATE_STARTUP_SEARCH:
            self._set_search_status(SEARCH_ACTIVE)
            before = self.startup_search.snapshot()
            next_pos, completed = self.startup_search.advance([self.j1, self.j2, self.j3, self.j4])
            self.j1, self.j2, self.j3, self.j4 = next_pos
            self._publish_joint_state()
            self.get_logger().info(
                f'state=startup_search phase={before["phase"]} '
                f'level={before["level_index"] + 1}/{before["level_count"]} '
                f'target_j3={before["target_j3"]:.3f} '
                f'J=({self.j1:.3f},{self.j2:.3f},{self.j3:.3f},{self.j4:.3f})'
            )
            if completed:
                self.search_requested = False
                self.search_complete = True
                self.state = STATE_IDLE
                self._set_search_status(SEARCH_COMPLETE)
                self.get_logger().warning('Startup book search completed without detection')
            return

        if self.state == STATE_NEXT_PAGE_LOCAL_SEARCH and self.next_page_search is not None:
            self._set_search_status(SEARCH_ACTIVE)
            next_pos, completed = self.next_page_search.advance([self.j1, self.j2, self.j3, self.j4])
            self.j1, self.j2, self.j3, self.j4 = next_pos
            self._publish_joint_state()
            if completed:
                self.get_logger().warning(
                    'Next-page local search failed; falling back to startup search'
                )
                self._enter_startup_search()

    def _publish_joint_state(self):
        msg_out = JointState()
        msg_out.header.stamp = self.get_clock().now().to_msg()
        msg_out.header.frame_id = 'base_link'
        msg_out.name = ['base_link_to_link1', 'link1_to_link2',
                        'link2_to_link3', 'link3_to_gripper_link']
        msg_out.position = [float(self.j1), float(self.j2),
                            float(self.j3), float(self.j4)]
        self.joint_pub.publish(msg_out)
        self.last_published_joints = list(msg_out.position)

    def _set_search_status(self, status):
        if status == self.search_status:
            return
        self.search_status = status
        self.search_status_pub.publish(Int8(data=status))

    def _set_prepare_status(self, status):
        if status == self.prepare_status:
            return
        self.prepare_status = status
        self.prepare_status_pub.publish(Int8(data=status))


def main():
    rclpy.init()
    node = ServoController()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
