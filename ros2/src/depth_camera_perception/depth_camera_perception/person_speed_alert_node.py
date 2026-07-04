from __future__ import annotations

import time
from typing import Optional

import rclpy
from cv_bridge import CvBridge
from rclpy.node import Node
from sensor_msgs.msg import CameraInfo, Image
from std_msgs.msg import String

from .depth_utils import estimate_bbox_depth_m, nearest_detection
from .person_detector import create_person_detector
from .person_speed_monitor import CameraModel, PersonObservation, PersonSpeedMonitor


class PersonSpeedAlertNode(Node):
    def __init__(self):
        super().__init__("person_speed_alert")
        self.declare_parameter("color_topic", "/camera/color/image_raw")
        self.declare_parameter("depth_topic", "/camera/depth/image_raw")
        self.declare_parameter("camera_info_topic", "/camera/color/camera_info")
        self.declare_parameter("alert_topic", "/depth_camera/person_speed_alert")
        self.declare_parameter("detector_backend", "ultralytics")
        self.declare_parameter("model_path", "/home/elf/ros2/yolov8n.pt")
        self.declare_parameter("confidence", 0.4)
        self.declare_parameter("roi_fraction", 0.5)
        self.declare_parameter("speed_threshold_mps", 1.5)
        self.declare_parameter("duration_threshold_s", 1.0)
        self.declare_parameter("alert_cooldown_s", 5.0)
        self.declare_parameter("max_sample_gap_s", 0.75)
        self.declare_parameter("process_period_sec", 0.2)
        self.declare_parameter("log_period_sec", 1.0)

        self._bridge = CvBridge()
        self._latest_color: Optional[Image] = None
        self._latest_depth: Optional[Image] = None
        self._camera_model: Optional[CameraModel] = None
        self._last_process_s = 0.0
        self._last_log_s = 0.0

        backend = str(self.get_parameter("detector_backend").value)
        model_path = str(self.get_parameter("model_path").value)
        confidence = float(self.get_parameter("confidence").value)
        self._detector = create_person_detector(backend, model_path, confidence)
        self._monitor = PersonSpeedMonitor(
            speed_threshold_mps=float(self.get_parameter("speed_threshold_mps").value),
            duration_threshold_s=float(self.get_parameter("duration_threshold_s").value),
            alert_cooldown_s=float(self.get_parameter("alert_cooldown_s").value),
            max_sample_gap_s=float(self.get_parameter("max_sample_gap_s").value),
        )

        color_topic = str(self.get_parameter("color_topic").value)
        depth_topic = str(self.get_parameter("depth_topic").value)
        camera_info_topic = str(self.get_parameter("camera_info_topic").value)
        alert_topic = str(self.get_parameter("alert_topic").value)
        self.create_subscription(Image, color_topic, self._on_color, 10)
        self.create_subscription(Image, depth_topic, self._on_depth, 10)
        self.create_subscription(CameraInfo, camera_info_topic, self._on_camera_info, 10)
        self._alert_pub = self.create_publisher(String, alert_topic, 10)
        self.create_timer(0.05, self._tick)
        self.get_logger().info(
            f"person speed alert listening color={color_topic} depth={depth_topic} "
            f"camera_info={camera_info_topic} alert_topic={alert_topic}"
        )

    def _on_color(self, msg: Image) -> None:
        self._latest_color = msg

    def _on_depth(self, msg: Image) -> None:
        self._latest_depth = msg

    def _on_camera_info(self, msg: CameraInfo) -> None:
        self._camera_model = CameraModel(
            width=int(msg.width),
            height=int(msg.height),
            fx=float(msg.k[0]),
            fy=float(msg.k[4]),
            cx=float(msg.k[2]),
            cy=float(msg.k[5]),
        )

    def _tick(self) -> None:
        if self._latest_color is None or self._latest_depth is None:
            return

        now = time.monotonic()
        process_period = float(self.get_parameter("process_period_sec").value)
        if now - self._last_process_s < process_period:
            return
        self._last_process_s = now

        color = self._bridge.imgmsg_to_cv2(self._latest_color, desired_encoding="bgr8")
        depth = self._bridge.imgmsg_to_cv2(self._latest_depth, desired_encoding="passthrough")
        camera = self._camera_model or CameraModel.approximate(width=color.shape[1], height=color.shape[0])
        roi_fraction = float(self.get_parameter("roi_fraction").value)

        detections = []
        for detection in self._detector.detect(color):
            distance_m = estimate_bbox_depth_m(depth, detection.bbox, fraction=roi_fraction)
            detections.append(detection.with_distance(distance_m))

        nearest = nearest_detection(detections)
        if nearest is None or nearest.distance_m is None:
            self._monitor.reset()
            self._log_periodic("persons=%d nearest=none speed=none" % len(detections))
            return

        observation = PersonObservation(
            timestamp_s=now,
            bbox=nearest.bbox,
            confidence=nearest.confidence,
            distance_m=nearest.distance_m,
            camera=camera,
            track_id=nearest.track_id,
        )
        result = self._monitor.update(observation)
        if result.alert_triggered and result.alert_event_json:
            msg = String()
            msg.data = result.alert_event_json
            self._alert_pub.publish(msg)
            self.get_logger().warn(f"ALERT {result.alert_event_json}")

        speed_text = "none" if result.speed_mps is None else "%.2fm/s" % result.speed_mps
        self._log_periodic(
            "persons=%d nearest distance=%.2fm conf=%.2f speed=%s over=%.2fs"
            % (
                len(detections),
                nearest.distance_m,
                nearest.confidence,
                speed_text,
                result.over_threshold_duration_s,
            )
        )

    def _log_periodic(self, text: str) -> None:
        now = time.monotonic()
        period = float(self.get_parameter("log_period_sec").value)
        if now - self._last_log_s >= period:
            self._last_log_s = now
            self.get_logger().info(text)


def main() -> None:
    rclpy.init()
    node = PersonSpeedAlertNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
