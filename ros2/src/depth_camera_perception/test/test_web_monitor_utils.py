import json
import unittest

from depth_camera_perception.web_monitor_utils import (
    BGR_GREEN,
    BGR_RED,
    FpsCounter,
    MonitorStatus,
    choose_box_color,
)


class WebMonitorUtilsTest(unittest.TestCase):
    def test_fps_counter_uses_recent_window(self):
        counter = FpsCounter(window_sec=1.0)
        for ts in [10.0, 10.25, 10.5, 10.75, 11.0]:
            counter.mark(ts)
        self.assertAlmostEqual(counter.fps(now_s=11.0), 4.0, places=2)

        counter.mark(12.0)
        self.assertAlmostEqual(counter.fps(now_s=12.0), 1.0, places=2)

    def test_fps_counter_returns_zero_after_all_samples_expire(self):
        counter = FpsCounter(window_sec=1.0)
        counter.mark(10.0)
        self.assertEqual(counter.fps(now_s=12.0), 0.0)

    def test_choose_box_color_turns_red_when_fast(self):
        self.assertEqual(choose_box_color(is_fast=False), BGR_GREEN)
        self.assertEqual(choose_box_color(is_fast=True), BGR_RED)

    def test_monitor_status_json_contains_fps_and_alert_state(self):
        status = MonitorStatus(
            camera_fps=30.0,
            inference_fps=4.8,
            stream_fps=4.0,
            image_width=640,
            image_height=480,
            person_count=2,
            nearest_distance_m=1.25,
            nearest_speed_mps=1.7,
            speed_threshold_mps=1.5,
            fast_active=True,
            alert_active=True,
            last_update_s=123.456,
            message="FAST",
        )
        payload = json.loads(status.to_json())
        self.assertEqual(payload["image"]["width"], 640)
        self.assertEqual(payload["image"]["height"], 480)
        self.assertEqual(payload["people"]["count"], 2)
        self.assertEqual(payload["alert"]["active"], True)
        self.assertEqual(payload["alert"]["message"], "FAST")
        self.assertAlmostEqual(payload["fps"]["inference"], 4.8)


if __name__ == "__main__":
    unittest.main()
