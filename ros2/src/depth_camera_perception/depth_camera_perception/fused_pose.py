from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FusedPoseConfig:
    publish_period_s: float = 0.02
    velocity_timeout_s: float = 0.30
    imu_timeout_s: float = 0.30
    max_step_s: float = 0.10
    imu_weight: float = 0.80
    imu_yaw_rate_sign: float = 1.0
    linear_x_scale: float = 1.0
    yaw_rate_scale: float = 1.0
    stationary_linear_epsilon: float = 0.02
    stationary_angular_epsilon: float = 0.03
    gyro_bias_alpha: float = 0.98


@dataclass
class Pose2D:
    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0
    distance_m: float = 0.0


class FusedPoseEstimator:
    def __init__(self, config: Optional[FusedPoseConfig] = None):
        self.config = config or FusedPoseConfig()
        self.pose = Pose2D()
        self.last_step_s: Optional[float] = None
        self.velocity_stamp_s: Optional[float] = None
        self.imu_stamp_s: Optional[float] = None
        self.linear_x = 0.0
        self.encoder_angular_z = 0.0
        self.imu_angular_z = 0.0
        self.gyro_bias_z = 0.0
        self.last_fused_linear_x = 0.0
        self.last_fused_angular_z = 0.0

    def reset(self, stamp_s: Optional[float] = None) -> None:
        self.pose = Pose2D()
        self.last_step_s = stamp_s
        self.velocity_stamp_s = None
        self.imu_stamp_s = None
        self.linear_x = 0.0
        self.encoder_angular_z = 0.0
        self.imu_angular_z = 0.0
        self.gyro_bias_z = 0.0
        self.last_fused_linear_x = 0.0
        self.last_fused_angular_z = 0.0

    def update_velocity(self, *, linear_x: float, angular_z: float, stamp_s: float) -> None:
        self.linear_x = float(linear_x) * float(self.config.linear_x_scale)
        self.encoder_angular_z = float(angular_z) * float(self.config.yaw_rate_scale)
        self.velocity_stamp_s = float(stamp_s)
        if self.last_step_s is None:
            self.last_step_s = self.velocity_stamp_s

    def update_imu(self, *, angular_z: float, stamp_s: float) -> None:
        self.imu_angular_z = (
            float(angular_z)
            * float(self.config.imu_yaw_rate_sign)
            * float(self.config.yaw_rate_scale)
        )
        self.imu_stamp_s = float(stamp_s)
        if self.last_step_s is None:
            self.last_step_s = self.imu_stamp_s

    def step(self, now_s: float) -> Pose2D:
        now = float(now_s)
        if self.last_step_s is None:
            self.last_step_s = now
            return self.pose

        dt = now - self.last_step_s
        self.last_step_s = now
        if dt <= 0.0:
            return self.pose
        dt = min(dt, self.config.max_step_s)

        velocity_fresh = _is_fresh(now, self.velocity_stamp_s, self.config.velocity_timeout_s)
        imu_fresh = _is_fresh(now, self.imu_stamp_s, self.config.imu_timeout_s)

        vx = self.linear_x if velocity_fresh else 0.0
        encoder_wz = self.encoder_angular_z if velocity_fresh else 0.0

        if imu_fresh:
            self._update_gyro_bias(vx, encoder_wz)
            imu_wz = self.imu_angular_z - self.gyro_bias_z
            if velocity_fresh:
                weight = min(1.0, max(0.0, self.config.imu_weight))
                wz = weight * imu_wz + (1.0 - weight) * encoder_wz
            else:
                wz = imu_wz
        else:
            wz = encoder_wz

        self.last_fused_linear_x = vx
        self.last_fused_angular_z = wz
        mid_yaw = self.pose.yaw + wz * dt / 2.0
        self.pose.x += vx * math.cos(mid_yaw) * dt
        self.pose.y += vx * math.sin(mid_yaw) * dt
        self.pose.yaw = wrap_angle(self.pose.yaw + wz * dt)
        self.pose.distance_m += abs(vx) * dt
        return self.pose

    def _update_gyro_bias(self, vx: float, encoder_wz: float) -> None:
        if self.config.gyro_bias_alpha >= 1.0:
            return
        if abs(vx) > self.config.stationary_linear_epsilon:
            return
        if abs(encoder_wz) > self.config.stationary_angular_epsilon:
            return
        alpha = min(0.999, max(0.0, self.config.gyro_bias_alpha))
        self.gyro_bias_z = alpha * self.gyro_bias_z + (1.0 - alpha) * self.imu_angular_z


def status_payload(
    estimator: FusedPoseEstimator,
    *,
    publish_hz: float,
    velocity_hz: float,
    imu_hz: float,
    source_domain_id: str,
) -> dict:
    pose = estimator.pose
    last_step_s = estimator.last_step_s
    velocity_fresh = _is_fresh(last_step_s, estimator.velocity_stamp_s, estimator.config.velocity_timeout_s)
    imu_fresh = _is_fresh(last_step_s, estimator.imu_stamp_s, estimator.config.imu_timeout_s)
    return {
        'pose': {
            'forward_m': round(pose.x, 3),
            'lateral_m': round(pose.y, 3),
            'distance_m': round(pose.distance_m, 3),
            'yaw_rad': round(pose.yaw, 4),
            'yaw_deg': round(math.degrees(pose.yaw), 2),
        },
        'velocity': {
            'linear_x_mps': round(estimator.linear_x, 3),
            'encoder_angular_z_rps': round(estimator.encoder_angular_z, 3),
            'imu_angular_z_rps': round(estimator.imu_angular_z, 3),
            'gyro_bias_z_rps': round(estimator.gyro_bias_z, 4),
            'fused_linear_x_mps': round(estimator.last_fused_linear_x, 3),
            'fused_angular_z_rps': round(estimator.last_fused_angular_z, 3),
        },
        'rates': {
            'publish_hz': round(float(publish_hz), 2),
            'velocity_hz': round(float(velocity_hz), 2),
            'imu_hz': round(float(imu_hz), 2),
        },
        'fresh': {
            'velocity': velocity_fresh,
            'imu': imu_fresh,
        },
        'config': {
            'publish_period_s': estimator.config.publish_period_s,
            'imu_weight': estimator.config.imu_weight,
            'linear_x_scale': estimator.config.linear_x_scale,
            'yaw_rate_scale': estimator.config.yaw_rate_scale,
            'velocity_timeout_s': estimator.config.velocity_timeout_s,
            'imu_timeout_s': estimator.config.imu_timeout_s,
        },
        'ros': {
            'domain_id': str(source_domain_id),
        },
    }


def wrap_angle(angle: float) -> float:
    wrapped = (float(angle) + math.pi) % (2.0 * math.pi) - math.pi
    if wrapped == -math.pi:
        return math.pi
    return wrapped


def _is_fresh(now_s: float, stamp_s: Optional[float], timeout_s: float) -> bool:
    if stamp_s is None:
        return False
    return now_s - stamp_s <= timeout_s
