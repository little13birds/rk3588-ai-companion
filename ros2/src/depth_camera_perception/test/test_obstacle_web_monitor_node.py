import numpy as np

from depth_camera_perception.obstacle_web_monitor_node import (
    ObstacleWebMonitorNode,
    _depth_to_bgr,
    _front_zone_bounds,
)
from depth_camera_perception.obstacle_web_utils import ObstacleWebStatus


def test_front_zone_bounds_match_guard_front_width():
    assert _front_zone_bounds(640) == (160, 480)


def test_overlay_front_zone_matches_guard_front_width():
    frame = np.zeros((120, 640, 3), dtype=np.uint8)
    status = ObstacleWebStatus(
        state='clear',
        reason='clear',
        front_distance_m=1.0,
        left_distance_m=None,
        right_distance_m=None,
        front_blocked=False,
        left_clear=False,
        right_clear=False,
        dry_run=True,
        output_linear_x=0.0,
        output_angular_z=0.0,
        cmd_age_s=0.0,
        depth_age_s=0.0,
        last_update_s=0.0,
    )

    ObstacleWebMonitorNode._draw_overlay(None, frame, status)

    front_green_fill = (8, 42, 21)
    assert tuple(int(value) for value in frame[70, 170]) == front_green_fill
    assert tuple(int(value) for value in frame[70, 470]) == front_green_fill


def test_depth_to_bgr_keeps_invalid_depth_black_and_colors_valid_depth():
    depth = np.array(
        [
            [np.nan, 0.8],
            [0.0, 2.0],
        ],
        dtype=np.float32,
    )

    frame = _depth_to_bgr(depth, width=4, height=4)

    assert frame.shape == (4, 4, 3)
    assert tuple(int(value) for value in frame[0, 0]) == (0, 0, 0)
    assert tuple(int(value) for value in frame[3, 3]) != (0, 0, 0)
