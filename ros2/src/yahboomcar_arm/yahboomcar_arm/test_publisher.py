#!/usr/bin/env python3
"""
模拟相机数据发布器 — 测试机械臂平缓移动
运行: ros2 run yahboomcar_arm test_publisher
"""

import time
import rclpy
from rclpy.node import Node
from yahboomcar_msgs.msg import BookPose


class TestPublisher(Node):
    def __init__(self):
        super().__init__("test_publisher")
        self.pub = self.create_publisher(BookPose, "/book_pose", 10)
        self.declare_parameter("rate", 20.0)
        self.rate = self.get_parameter("rate").value
        dt = 1.0 / self.rate
        self.timer = self.create_timer(dt, self._publish)
        self._t = 0.0

        self.get_logger().info("Publishing simulated BookPose at %.0f Hz" % self.rate)

    def _publish(self):
        self._t += 1.0 / self.rate
        msg = BookPose()

        # ======== 修改下面这 4 组值来测试不同场景 ========

        # 场景A: 书本在右下方，慢慢靠近
        msg.dx_px = 80.0
        msg.dy_px = 25.0
        msg.pitch_ratio = 1.0           # 1.0 = 相机已垂直
        msg.yaw_ratio = 1.0
        msg.roll_deg = 0.0
        msg.avg_edge_px = 250.0         # 距离合适

        # ===============================================

        self.pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = TestPublisher()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
