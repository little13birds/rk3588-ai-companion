"""ROS RGB image subscriber used by safety monitoring."""
from __future__ import annotations

import threading
import time
from typing import Optional, Tuple

import numpy as np


class RosRgbCamera:
    def __init__(self, topic: str = "/camera/color/image_raw", qos_depth: int = 4):
        self.topic = topic
        self.qos_depth = qos_depth
        self._lock = threading.Lock()
        self._latest_bgr: Optional[np.ndarray] = None
        self._latest_stamp = 0.0
        self._received = 0
        self._converted = 0
        self._stop = threading.Event()
        self._thread = None
        self._node = None
        self._rclpy = None
        self._owns_rclpy = False
        self._external_shutdown_exception = None

    def start(self) -> None:
        import rclpy
        from rclpy.executors import ExternalShutdownException
        from rclpy.signals import SignalHandlerOptions
        from cv_bridge import CvBridge
        from rclpy.node import Node
        from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
        from sensor_msgs.msg import Image

        if not rclpy.ok():
            rclpy.init(args=None, signal_handler_options=SignalHandlerOptions.NO)
            self._owns_rclpy = True

        bridge = CvBridge()
        qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=self.qos_depth,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
        )

        outer = self

        class _SafetyCameraNode(Node):
            def __init__(self):
                super().__init__("cloud_model_safety_rgb")
                self.create_subscription(Image, outer.topic, self._on_rgb, qos)

            def _on_rgb(self, msg: Image) -> None:
                try:
                    frame = bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
                except Exception as exc:
                    self.get_logger().warn(f"safety rgb conversion failed: {exc}")
                    return
                with outer._lock:
                    outer._latest_bgr = np.ascontiguousarray(frame)
                    outer._latest_stamp = time.monotonic()
                    outer._received += 1
                    outer._converted += 1

        self._rclpy = rclpy
        self._external_shutdown_exception = ExternalShutdownException
        self._node = _SafetyCameraNode()
        self._thread = threading.Thread(target=self._spin_loop, name="safety-ros-camera", daemon=True)
        self._thread.start()

    def _spin_loop(self) -> None:
        while not self._stop.is_set() and self._rclpy and self._rclpy.ok():
            try:
                self._rclpy.spin_once(self._node, timeout_sec=0.1)
            except self._external_shutdown_exception:
                break

    def latest_bgr(self) -> Tuple[Optional[np.ndarray], float]:
        with self._lock:
            if self._latest_bgr is None:
                return None, 0.0
            return self._latest_bgr.copy(), self._latest_stamp

    def stats(self) -> dict:
        with self._lock:
            return {
                "received": self._received,
                "converted": self._converted,
                "has_frame": self._latest_bgr is not None,
                "age_sec": time.monotonic() - self._latest_stamp if self._latest_stamp else None,
            }

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._node:
            try:
                self._node.destroy_node()
            except Exception:
                pass
            self._node = None
        if self._owns_rclpy and self._rclpy and self._rclpy.ok():
            try:
                self._rclpy.shutdown()
            except Exception:
                pass
