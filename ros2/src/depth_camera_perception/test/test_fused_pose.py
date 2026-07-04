import math

import pytest

from depth_camera_perception.fused_pose import (
    FusedPoseConfig,
    FusedPoseEstimator,
    status_payload,
    wrap_angle,
)


def _run_50hz_with_10hz_sources(
    estimator: FusedPoseEstimator,
    *,
    duration_s: float,
    linear_x: float,
    encoder_angular_z: float = 0.0,
    imu_angular_z: float | None = None,
) -> None:
    estimator.update_velocity(linear_x=linear_x, angular_z=encoder_angular_z, stamp_s=0.0)
    if imu_angular_z is not None:
        estimator.update_imu(angular_z=imu_angular_z, stamp_s=0.0)

    step_count = int(round(duration_s / 0.02))
    for index in range(1, step_count + 1):
        now = index * 0.02
        if index % 5 == 0:
            estimator.update_velocity(
                linear_x=linear_x,
                angular_z=encoder_angular_z,
                stamp_s=now,
            )
            if imu_angular_z is not None:
                estimator.update_imu(angular_z=imu_angular_z, stamp_s=now)
        estimator.step(now)


def test_estimator_integrates_forward_distance_at_50hz():
    estimator = FusedPoseEstimator(FusedPoseConfig(imu_weight=0.0))
    _run_50hz_with_10hz_sources(estimator, duration_s=1.0, linear_x=0.4)

    assert estimator.pose.x == pytest.approx(0.4, abs=1e-3)
    assert estimator.pose.y == pytest.approx(0.0, abs=1e-3)
    assert estimator.pose.distance_m == pytest.approx(0.4, abs=1e-3)
    assert estimator.pose.yaw == pytest.approx(0.0, abs=1e-3)
    assert estimator.config.publish_period_s == pytest.approx(0.02)


def test_estimator_uses_imu_yaw_rate_when_available():
    estimator = FusedPoseEstimator(FusedPoseConfig(imu_weight=1.0, gyro_bias_alpha=1.0))
    _run_50hz_with_10hz_sources(
        estimator,
        duration_s=1.0,
        linear_x=0.0,
        imu_angular_z=math.pi / 2.0,
    )

    assert estimator.pose.yaw == pytest.approx(math.pi / 2.0, abs=1e-3)


def test_estimator_applies_linear_and_yaw_calibration_scales():
    estimator = FusedPoseEstimator(
        FusedPoseConfig(
            imu_weight=1.0,
            gyro_bias_alpha=1.0,
            linear_x_scale=2.0,
            yaw_rate_scale=2.0,
        )
    )
    _run_50hz_with_10hz_sources(
        estimator,
        duration_s=1.0,
        linear_x=0.2,
        imu_angular_z=math.pi / 4.0,
    )

    assert estimator.pose.distance_m == pytest.approx(0.4, abs=1e-3)
    assert estimator.pose.yaw == pytest.approx(math.pi / 2.0, abs=1e-3)


def test_estimator_stops_integrating_when_velocity_is_stale():
    estimator = FusedPoseEstimator(FusedPoseConfig(imu_weight=0.0, velocity_timeout_s=0.20))
    estimator.update_velocity(linear_x=0.4, angular_z=0.0, stamp_s=0.0)

    estimator.step(0.02)
    estimator.step(0.50)

    assert estimator.pose.x == pytest.approx(0.008, abs=1e-3)


def test_status_payload_contains_distance_direction_and_rates():
    estimator = FusedPoseEstimator(FusedPoseConfig())
    estimator.update_velocity(linear_x=0.4, angular_z=0.0, stamp_s=0.0)
    estimator.step(0.02)

    payload = status_payload(
        estimator,
        publish_hz=50.0,
        velocity_hz=10.0,
        imu_hz=10.0,
        source_domain_id='30',
    )

    assert payload['pose']['forward_m'] == pytest.approx(0.008)
    assert payload['pose']['yaw_deg'] == pytest.approx(0.0)
    assert payload['rates']['publish_hz'] == pytest.approx(50.0)
    assert payload['rates']['velocity_hz'] == pytest.approx(10.0)
    assert payload['ros']['domain_id'] == '30'


def test_wrap_angle_keeps_heading_in_pi_range():
    assert wrap_angle(3.5) == pytest.approx(3.5 - 2.0 * math.pi)
    assert wrap_angle(-3.5) == pytest.approx(-3.5 + 2.0 * math.pi)
