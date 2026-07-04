#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Camera HTTP → ROS2 BookPose Bridge
Polls image_cropping HTTP server and publishes to /book_pose

Handles: normal (YOLO direct), tracking (KLT bridged), lost (stop)
"""

import json
import time
import urllib.request

import rclpy
from rclpy.node import Node
from yahboomcar_msgs.msg import BookPose


class CameraBridge(Node):
    def __init__(self):
        super().__init__("camera_bridge")
        self.declare_parameter("url", "http://192.168.1.113:8765/")
        self.declare_parameter("rate", 20.0)
        self.url = self.get_parameter("url").value
        self.rate = self.get_parameter("rate").value

        self.pub = self.create_publisher(BookPose, "/book_pose", 10)
        dt = 1.0 / self.rate
        self.timer = self.create_timer(dt, self._loop)

        self._frame_count = 0
        self._last_status = "?"

        self.get_logger().info("Polling %s at %.0f Hz" % (self.url, self.rate))

    def _loop(self):
        try:
            resp = urllib.request.urlopen(self.url, timeout=0.3)
            data = json.loads(resp.read())
            self._frame_count += 1

            now = time.time()
            latency = (now - data.get("timestamp", now)) * 1000
            status = data.get("status", "?")
            detected = data.get("detected", False)

            # Normal (YOLO) or Tracking (KLT) — both have valid pose data
            if status in ("normal", "tracking"):
                msg = BookPose()
                msg.dx_px = float(data.get("dx_px", 0.0))
                msg.dy_px = float(data.get("dy_px", 0.0))
                msg.pitch_ratio = float(data.get("pitch_ratio", 1.0))
                msg.yaw_ratio = float(data.get("yaw_ratio", 1.0))
                msg.roll_deg = float(data.get("roll_deg", 0.0))
                msg.avg_edge_px = float(data.get("avg_edge_px", 0.0))
                msg.mask_area = float(data.get("mask_area", 0.0))
                self.pub.publish(msg)

                if status != self._last_status or self._frame_count % 100 == 0:
                    health = data.get("health", 0)
                    self.get_logger().info(
                        "[%s h=%.2f] dx=%+.1f dy=%+.1f pitch=%.3f edge=%.0f area=%.0f lat=%.0fms"
                        % (status, health, msg.dx_px, msg.dy_px,
                           msg.pitch_ratio, msg.avg_edge_px, msg.mask_area, latency)
                    )
                self._last_status = status

            elif status == "lost":
                if self._last_status != "lost":
                    self.get_logger().warn("[LOST] %s" % data.get("message", "book lost"))
                self._last_status = "lost"
                # don't publish → arm controller times out and stops

        except urllib.error.URLError as e:
            self.get_logger().warn("HTTP error: %s" % str(e), throttle_duration_sec=5.0)
        except Exception as e:
            self.get_logger().warn("Error: %s" % str(e), throttle_duration_sec=5.0)


def main(args=None):
    rclpy.init(args=args)
    node = CameraBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
