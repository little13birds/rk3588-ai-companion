import json
import unittest

from depth_camera_perception.obstacle_web_utils import (
    obstacle_level,
    parse_guard_status,
    status_payload,
)


class ObstacleWebUtilsTest(unittest.TestCase):
    def test_parse_blocked_status_reports_danger_level(self):
        status = parse_guard_status(
            json.dumps(
                {
                    'state': 'blocked',
                    'reason': 'blocked',
                    'front_distance_m': 0.34,
                    'left_distance_m': 1.20,
                    'right_distance_m': 0.45,
                    'front_blocked': True,
                    'left_clear': True,
                    'right_clear': False,
                    'dry_run': False,
                    'output_linear_x': 0.0,
                    'output_angular_z': 0.25,
                    'cmd_age_s': 0.1,
                    'depth_age_s': 0.02,
                }
            ),
            now_s=100.0,
        )
        self.assertEqual(obstacle_level(status), 'danger')
        self.assertEqual(status.reason, 'blocked')
        self.assertAlmostEqual(status.front_distance_m, 0.34)
        self.assertEqual(status.output_angular_z, 0.25)

    def test_payload_rounds_distances_and_marks_clear(self):
        status = parse_guard_status(
            json.dumps(
                {
                    'state': 'clear',
                    'reason': 'clear',
                    'front_distance_m': 1.2971,
                    'left_distance_m': 1.805,
                    'right_distance_m': 1.602,
                    'front_blocked': False,
                    'left_clear': True,
                    'right_clear': True,
                    'dry_run': True,
                    'output_linear_x': 0.08,
                    'output_angular_z': 0.0,
                    'cmd_age_s': 0.2,
                    'depth_age_s': 0.01,
                }
            ),
            now_s=120.0,
        )
        payload = status_payload(status, camera_fps=12.345, stream_fps=4.0, image_width=640, image_height=480)
        self.assertEqual(payload['state']['level'], 'clear')
        self.assertEqual(payload['mode']['dry_run'], True)
        self.assertEqual(payload['zones']['front']['distance_m'], 1.297)
        self.assertEqual(payload['output']['linear_x'], 0.08)
        self.assertEqual(payload['image']['width'], 640)

    def test_payload_includes_complete_bypass_state(self):
        status = parse_guard_status(
            json.dumps(
                {
                    'state': 'clear',
                    'reason': 'return_heading',
                    'front_distance_m': 1.2,
                    'left_distance_m': 1.5,
                    'right_distance_m': 1.4,
                    'front_blocked': False,
                    'left_clear': True,
                    'right_clear': True,
                    'dry_run': True,
                    'output_linear_x': 0.0,
                    'output_angular_z': -0.25,
                    'cmd_age_s': 0.1,
                    'depth_age_s': 0.02,
                    'pose_age_s': 0.03,
                    'avoidance_phase': 'return_heading',
                    'target_yaw_rad': 0.0,
                    'yaw_error_rad': -0.32,
                    'bypass_distance_m': 0.51,
                }
            ),
            now_s=125.0,
        )
        payload = status_payload(status, camera_fps=10.0, stream_fps=5.0, image_width=640, image_height=480)

        self.assertEqual(payload['avoidance']['phase'], 'return_heading')
        self.assertEqual(payload['avoidance']['bypass_distance_m'], 0.51)
        self.assertEqual(payload['avoidance']['yaw_error_rad'], -0.32)
        self.assertEqual(payload['age']['pose_s'], 0.03)

    def test_avoidance_substate_takes_display_priority_over_clear_front(self):
        status = parse_guard_status(
            json.dumps(
                {
                    'state': 'clear',
                    'reason': 'avoid_forward_side_clear_hold',
                    'front_distance_m': 1.2,
                    'left_distance_m': 1.5,
                    'right_distance_m': 0.55,
                    'front_blocked': False,
                    'left_clear': True,
                    'right_clear': False,
                    'dry_run': False,
                    'output_linear_x': 0.2,
                    'output_angular_z': 0.0,
                    'cmd_age_s': 0.1,
                    'depth_age_s': 0.02,
                    'pose_age_s': 0.01,
                    'avoidance_phase': 'avoid_forward',
                    'tracked_side': 'right',
                }
            ),
            now_s=125.5,
        )
        payload = status_payload(status, camera_fps=10.0, stream_fps=5.0, image_width=640, image_height=480)

        self.assertEqual(payload['state']['level'], 'warning')
        self.assertEqual(payload['state']['message'], '侧边清空保持中')
        self.assertEqual(payload['state']['reason'], 'avoid_forward_side_clear_hold')
        self.assertEqual(payload['avoidance']['phase'], 'avoid_forward')

    def test_avoidance_min_forward_display_is_not_front_clear(self):
        status = parse_guard_status(
            json.dumps(
                {
                    'state': 'clear',
                    'reason': 'avoid_forward_min_forward',
                    'front_distance_m': 1.2,
                    'left_distance_m': 1.5,
                    'right_distance_m': 1.4,
                    'front_blocked': False,
                    'left_clear': True,
                    'right_clear': True,
                    'dry_run': False,
                    'output_linear_x': 0.2,
                    'output_angular_z': 0.0,
                    'cmd_age_s': 0.1,
                    'depth_age_s': 0.02,
                    'pose_age_s': 0.01,
                    'avoidance_phase': 'avoid_forward',
                }
            ),
            now_s=125.6,
        )
        payload = status_payload(status, camera_fps=10.0, stream_fps=5.0, image_width=640, image_height=480)

        self.assertEqual(payload['state']['level'], 'warning')
        self.assertEqual(payload['state']['message'], '避障前进，延迟回正')

    def test_avoidance_side_steer_display_names_obstacle_side(self):
        status = parse_guard_status(
            json.dumps(
                {
                    'state': 'clear',
                    'reason': 'avoid_forward_side_steer_right',
                    'front_distance_m': 1.2,
                    'left_distance_m': 0.5,
                    'right_distance_m': 1.4,
                    'front_blocked': False,
                    'left_clear': False,
                    'right_clear': True,
                    'dry_run': False,
                    'output_linear_x': 0.2,
                    'output_angular_z': -0.25,
                    'cmd_age_s': 0.1,
                    'depth_age_s': 0.02,
                    'pose_age_s': 0.01,
                    'avoidance_phase': 'avoid_forward',
                    'tracked_side': 'left',
                }
            ),
            now_s=125.7,
        )
        payload = status_payload(status, camera_fps=10.0, stream_fps=5.0, image_width=640, image_height=480)

        self.assertEqual(payload['state']['level'], 'warning')
        self.assertEqual(payload['state']['message'], '侧边避让，左侧障碍')

    def test_front_turn_clear_hold_display_is_not_front_clear(self):
        status = parse_guard_status(
            json.dumps(
                {
                    'state': 'clear',
                    'reason': 'front_turn_clear_hold',
                    'front_distance_m': 1.2,
                    'left_distance_m': 1.4,
                    'right_distance_m': 1.6,
                    'front_blocked': False,
                    'left_clear': True,
                    'right_clear': True,
                    'dry_run': False,
                    'output_linear_x': 0.0,
                    'output_angular_z': 0.25,
                    'cmd_age_s': 0.1,
                    'depth_age_s': 0.02,
                    'pose_age_s': 0.01,
                    'avoidance_phase': 'turn_away',
                }
            ),
            now_s=125.8,
        )
        payload = status_payload(status, camera_fps=10.0, stream_fps=5.0, image_width=640, image_height=480)

        self.assertEqual(payload['state']['level'], 'warning')
        self.assertEqual(payload['state']['message'], '前方清空，延迟停止转弯')

    def test_exit_hold_display_is_not_front_clear(self):
        status = parse_guard_status(
            json.dumps(
                {
                    'state': 'clear',
                    'reason': 'avoid_forward_exit_hold',
                    'front_distance_m': 1.2,
                    'left_distance_m': 1.4,
                    'right_distance_m': 1.6,
                    'front_blocked': False,
                    'left_clear': True,
                    'right_clear': True,
                    'dry_run': False,
                    'output_linear_x': 0.2,
                    'output_angular_z': 0.0,
                    'cmd_age_s': 0.1,
                    'depth_age_s': 0.02,
                    'pose_age_s': 0.01,
                    'avoidance_phase': 'exit_forward_hold',
                }
            ),
            now_s=125.9,
        )
        payload = status_payload(status, camera_fps=10.0, stream_fps=5.0, image_width=640, image_height=480)

        self.assertEqual(payload['state']['level'], 'warning')
        self.assertEqual(payload['state']['message'], '回正完成，保持前进确认')

    def test_avoidance_return_heading_display_is_not_front_clear(self):
        status = parse_guard_status(
            json.dumps(
                {
                    'state': 'clear',
                    'reason': 'avoid_forward_return_heading',
                    'front_distance_m': 1.2,
                    'left_distance_m': 1.5,
                    'right_distance_m': 1.4,
                    'front_blocked': False,
                    'left_clear': True,
                    'right_clear': True,
                    'dry_run': False,
                    'output_linear_x': 0.2,
                    'output_angular_z': -0.25,
                    'cmd_age_s': 0.1,
                    'depth_age_s': 0.02,
                    'pose_age_s': 0.01,
                    'avoidance_phase': 'avoid_forward',
                    'tracked_side': 'right',
                }
            ),
            now_s=125.8,
        )
        payload = status_payload(status, camera_fps=10.0, stream_fps=5.0, image_width=640, image_height=480)

        self.assertEqual(payload['state']['level'], 'warning')
        self.assertEqual(payload['state']['message'], '避障回正中')

    def test_payload_includes_line_following_debug_state(self):
        status = parse_guard_status(
            json.dumps(
                {
                    'state': 'clear',
                    'reason': 'reacquire_side',
                    'front_distance_m': 1.2,
                    'left_distance_m': 1.5,
                    'right_distance_m': 0.55,
                    'front_blocked': False,
                    'left_clear': True,
                    'right_clear': False,
                    'dry_run': False,
                    'output_linear_x': 0.1,
                    'output_angular_z': -0.2,
                    'cmd_age_s': 0.1,
                    'depth_age_s': 0.02,
                    'pose_age_s': 0.01,
                    'avoidance_phase': 'reacquire_side',
                    'tracked_side': 'right',
                    'forward_offset_m': 0.42,
                    'lateral_offset_m': 0.18,
                    'heading_error_rad': -0.31,
                    'reacquire_turn_rad': 1.2,
                    'pose_stalled': False,
                }
            ),
            now_s=126.0,
        )
        payload = status_payload(status, camera_fps=10.0, stream_fps=5.0, image_width=640, image_height=480)

        self.assertEqual(payload['avoidance']['phase'], 'reacquire_side')
        self.assertEqual(payload['avoidance']['tracked_side'], 'right')
        self.assertEqual(payload['avoidance']['forward_offset_m'], 0.42)
        self.assertEqual(payload['avoidance']['lateral_offset_m'], 0.18)
        self.assertEqual(payload['avoidance']['heading_error_rad'], -0.31)
        self.assertEqual(payload['avoidance']['reacquire_turn_rad'], 1.2)
        self.assertFalse(payload['avoidance']['pose_stalled'])

    def test_invalid_json_becomes_waiting_status(self):
        status = parse_guard_status('not json', now_s=130.0)
        self.assertEqual(status.reason, 'invalid_status')
        self.assertEqual(obstacle_level(status), 'waiting')

    def test_payload_includes_depth_quality_and_depth_fps(self):
        status = parse_guard_status(
            json.dumps(
                {
                    'state': 'blocked',
                    'reason': 'blocked',
                    'front_distance_m': 2.0,
                    'left_distance_m': 1.2,
                    'right_distance_m': None,
                    'front_blocked': True,
                    'left_clear': True,
                    'right_clear': False,
                    'front_valid_fraction': 0.42,
                    'front_invalid_fraction': 0.58,
                    'left_valid_fraction': 0.71,
                    'left_invalid_fraction': 0.29,
                    'right_valid_fraction': 0.0,
                    'right_invalid_fraction': 1.0,
                    'dry_run': False,
                    'output_linear_x': 0.0,
                    'output_angular_z': 0.0,
                    'cmd_age_s': None,
                    'depth_age_s': 0.02,
                }
            ),
            now_s=135.0,
        )
        payload = status_payload(
            status,
            camera_fps=12.0,
            depth_fps=24.0,
            stream_fps=5.0,
            image_width=1280,
            image_height=480,
        )

        self.assertEqual(payload['fps']['depth'], 24.0)
        self.assertEqual(payload['zones']['front']['distance_m'], 2.0)
        self.assertEqual(payload['zones']['front']['invalid_fraction'], 0.58)
        self.assertEqual(payload['zones']['left']['valid_fraction'], 0.71)
        self.assertEqual(payload['zones']['right']['invalid_fraction'], 1.0)

    def test_null_distances_are_displayed_as_unsafe(self):
        status = parse_guard_status(
            json.dumps(
                {
                    'state': 'clear',
                    'reason': 'clear',
                    'front_distance_m': None,
                    'left_distance_m': None,
                    'right_distance_m': None,
                    'front_blocked': False,
                    'left_clear': True,
                    'right_clear': True,
                    'dry_run': False,
                    'output_linear_x': 0.0,
                    'output_angular_z': 0.0,
                    'cmd_age_s': 0.1,
                    'depth_age_s': 0.02,
                }
            ),
            now_s=140.0,
        )
        payload = status_payload(status, camera_fps=0.0, stream_fps=0.0, image_width=640, image_height=480)
        self.assertEqual(obstacle_level(status), 'danger')
        self.assertTrue(payload['zones']['front']['blocked'])
        self.assertFalse(payload['zones']['left']['clear'])
        self.assertFalse(payload['zones']['right']['clear'])


if __name__ == '__main__':
    unittest.main()
