from __future__ import annotations

import time
from typing import Optional

import rclpy
from cv_bridge import CvBridge
from rclpy.node import Node
from sensor_msgs.msg import Image

from .depth_utils import estimate_bbox_depth_m, nearest_detection
from .person_detector import create_person_detector


class PerceptionSmokeTest(Node):
    def __init__(self):
        super().__init__("depth_perception_smoke_test")
        self.declare_parameter("color_topic", "/camera/color/image_raw")
        self.declare_parameter("depth_topic", "/camera/depth/image_raw")
        self.declare_parameter("detector_backend", "ultralytics")
        self.declare_parameter("model_path", "yolov8n.pt")
        self.declare_parameter("confidence", 0.4)
        self.declare_parameter("roi_fraction", 0.5)
        self.declare_parameter("log_period_sec", 1.0)

        self._bridge = CvBridge()
        self._latest_color: Optional[Image] = None
        self._latest_depth: Optional[Image] = None
        self._last_log = 0.0

        backend = self.get_parameter("detector_backend").value
        model_path = self.get_parameter("model_path").value
        confidence = float(self.get_parameter("confidence").value)
        self._detector = create_person_detector(backend, model_path, confidence)

        color_topic = self.get_parameter("color_topic").value
        depth_topic = self.get_parameter("depth_topic").value
        self.create_subscription(Image, color_topic, self._on_color, 10)
        self.create_subscription(Image, depth_topic, self._on_depth, 10)
        self.create_timer(0.2, self._tick)
        self.get_logger().info(
            f"listening color={color_topic} depth={depth_topic} backend={backend} model={model_path}"
        )

    def _on_color(self, msg: Image) -> None:
        self._latest_color = msg

    def _on_depth(self, msg: Image) -> None:
        self._latest_depth = msg

    def _tick(self) -> None:
        if self._latest_color is None or self._latest_depth is None:
            return

        now = time.monotonic()
        period = float(self.get_parameter("log_period_sec").value)
        if now - self._last_log < period:
            return
        self._last_log = now

        color = self._bridge.imgmsg_to_cv2(self._latest_color, desired_encoding="bgr8")
        depth = self._bridge.imgmsg_to_cv2(self._latest_depth, desired_encoding="passthrough")
        roi_fraction = float(self.get_parameter("roi_fraction").value)

        detections = []
        for detection in self._detector.detect(color):
            distance_m = estimate_bbox_depth_m(depth, detection.bbox, fraction=roi_fraction)
            detections.append(detection.with_distance(distance_m))

        nearest = nearest_detection(detections)
        if nearest is None:
            self.get_logger().info(f"persons={len(detections)} nearest=none")
            return

        x1, y1, x2, y2 = nearest.bbox
        self.get_logger().info(
            "persons=%d nearest distance=%.2fm conf=%.2f bbox=(%.0f,%.0f,%.0f,%.0f)"
            % (len(detections), nearest.distance_m, nearest.confidence, x1, y1, x2, y2)
        )


def main() -> None:
    rclpy.init()
    node = PerceptionSmokeTest()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
