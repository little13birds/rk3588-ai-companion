#!/usr/bin/env python3
"""Bridge to avoid timestamp collisions in Cartographer.
   /wheel_odom → /odom_fixed (+1ms)
   /imu/data_raw → /imu_fixed (+2ms)
"""
import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Imu


class TimestampBridge(Node):
    def __init__(self):
        super().__init__("timestamp_bridge")
        # Odom (wheel encoder)
        self.odom_sub = self.create_subscription(Odometry, "/wheel_odom", self.odom_cb, 10)
        self.odom_pub = self.create_publisher(Odometry, "/odom_fixed", 10)
        # IMU
        self.imu_sub = self.create_subscription(Imu, "/imu/data_raw", self.imu_cb, 10)
        self.imu_pub = self.create_publisher(Imu, "/imu_fixed", 10)
        self.get_logger().info("Bridge: /wheel_odom->/odom_fixed(+1ms)  /imu/data_raw->/imu_fixed(+2ms)")

    def _shift(self, stamp, ms):
        stamp.nanosec += ms * 1_000_000
        if stamp.nanosec >= 1_000_000_000:
            stamp.nanosec -= 1_000_000_000
            stamp.sec += 1

    def odom_cb(self, msg):
        self._shift(msg.header.stamp, 1)
        self.odom_pub.publish(msg)

    def imu_cb(self, msg):
        self._shift(msg.header.stamp, 2)
        self.imu_pub.publish(msg)


def main():
    rclpy.init()
    rclpy.spin(TimestampBridge())
    rclpy.shutdown()

if __name__ == "__main__":
    main()
