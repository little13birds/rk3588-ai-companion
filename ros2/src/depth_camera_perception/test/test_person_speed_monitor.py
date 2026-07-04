import json
import unittest

from depth_camera_perception.person_speed_monitor import (
    CameraModel,
    PersonObservation,
    PersonSpeedMonitor,
    project_bbox_to_camera_xyz,
)


class PersonSpeedMonitorTest(unittest.TestCase):
    def test_projects_bbox_center_to_camera_xyz(self):
        camera = CameraModel(width=640, height=480, fx=600.0, fy=600.0, cx=320.0, cy=240.0)
        xyz = project_bbox_to_camera_xyz((320, 200, 360, 260), distance_m=2.0, camera=camera)
        self.assertAlmostEqual(xyz[0], 0.0667, places=3)
        self.assertAlmostEqual(xyz[1], -0.0333, places=3)
        self.assertAlmostEqual(xyz[2], 2.0, places=3)

    def test_first_observation_has_no_speed_and_no_alert(self):
        monitor = PersonSpeedMonitor(speed_threshold_mps=1.5, duration_threshold_s=1.0)
        obs = PersonObservation(
            timestamp_s=10.0,
            bbox=(300, 200, 340, 260),
            confidence=0.9,
            distance_m=2.0,
            camera=CameraModel(width=640, height=480, fx=600.0, fy=600.0, cx=320.0, cy=240.0),
        )
        result = monitor.update(obs)
        self.assertIsNone(result.speed_mps)
        self.assertFalse(result.alert_triggered)

    def test_alerts_after_speed_stays_over_threshold_for_one_second(self):
        monitor = PersonSpeedMonitor(
            speed_threshold_mps=1.5,
            duration_threshold_s=1.0,
            alert_cooldown_s=3.0,
            max_sample_gap_s=0.6,
        )
        camera = CameraModel(width=640, height=480, fx=600.0, fy=600.0, cx=320.0, cy=240.0)
        samples = [
            PersonObservation(0.0, (300, 200, 340, 260), 0.9, 2.0, camera),
            PersonObservation(0.5, (300, 200, 340, 260), 0.9, 2.9, camera),
            PersonObservation(1.0, (300, 200, 340, 260), 0.9, 3.8, camera),
        ]
        results = [monitor.update(sample) for sample in samples]
        self.assertFalse(results[1].alert_triggered)
        self.assertTrue(results[2].alert_triggered)
        self.assertGreater(results[2].speed_mps, 1.5)
        event = json.loads(results[2].alert_event_json)
        self.assertEqual(event["event"], "person_speed_alert")
        self.assertEqual(event["target_type"], "person")
        self.assertAlmostEqual(event["threshold_mps"], 1.5)

    def test_resets_over_threshold_window_when_speed_drops(self):
        monitor = PersonSpeedMonitor(speed_threshold_mps=1.5, duration_threshold_s=1.0)
        camera = CameraModel(width=640, height=480, fx=600.0, fy=600.0, cx=320.0, cy=240.0)
        samples = [
            PersonObservation(0.0, (300, 200, 340, 260), 0.9, 2.0, camera),
            PersonObservation(0.5, (300, 200, 340, 260), 0.9, 2.9, camera),
            PersonObservation(1.0, (300, 200, 340, 260), 0.9, 3.0, camera),
            PersonObservation(1.5, (300, 200, 340, 260), 0.9, 4.0, camera),
        ]
        results = [monitor.update(sample) for sample in samples]
        self.assertFalse(any(result.alert_triggered for result in results))

    def test_cooldown_prevents_repeated_alert_spam(self):
        monitor = PersonSpeedMonitor(
            speed_threshold_mps=1.5,
            duration_threshold_s=1.0,
            alert_cooldown_s=3.0,
            max_sample_gap_s=0.6,
        )
        camera = CameraModel(width=640, height=480, fx=600.0, fy=600.0, cx=320.0, cy=240.0)
        samples = [
            PersonObservation(0.0, (300, 200, 340, 260), 0.9, 2.0, camera),
            PersonObservation(0.5, (300, 200, 340, 260), 0.9, 2.9, camera),
            PersonObservation(1.0, (300, 200, 340, 260), 0.9, 3.8, camera),
            PersonObservation(1.5, (300, 200, 340, 260), 0.9, 4.7, camera),
            PersonObservation(4.2, (300, 200, 340, 260), 0.9, 6.0, camera),
            PersonObservation(4.7, (300, 200, 340, 260), 0.9, 6.9, camera),
            PersonObservation(5.2, (300, 200, 340, 260), 0.9, 7.8, camera),
        ]
        results = [monitor.update(sample) for sample in samples]
        alert_times = [result.timestamp_s for result in results if result.alert_triggered]
        self.assertEqual(alert_times, [1.0, 5.2])


if __name__ == "__main__":
    unittest.main()
