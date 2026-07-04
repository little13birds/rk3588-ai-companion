#!/usr/bin/env python3
"""Wheel odometry: /vel_raw → integration → /wheel_odom + odom→base_link TF.
   Wheel: 53.5mm dia, 180mm wheelbase."""
import math
import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Twist, TransformStamped, Quaternion
from tf2_ros import TransformBroadcaster


class WheelOdom(Node):
    def __init__(self):
        super().__init__("wheel_odom")
        self.declare_parameter("wheel_diameter", 0.0535)
        self.declare_parameter("wheelbase", 0.180)
        self.declare_parameter("odom_frame", "odom")
        self.declare_parameter("base_frame", "base_link")

        self.wheel_d = self.get_parameter("wheel_diameter").value
        self.wheelbase = self.get_parameter("wheelbase").value
        self.odom_frame = self.get_parameter("odom_frame").value
        self.base_frame = self.get_parameter("base_frame").value

        self.sub = self.create_subscription(Twist, "/vel_raw", self.cb, 10)
        self.pub = self.create_publisher(Odometry, "/wheel_odom", 10)
        self.tf_broadcaster = TransformBroadcaster(self)

        self.x = 0.0
        self.y = 0.0
        self.yaw = 0.0
        self.last_time = self.get_clock().now()

        self.get_logger().info(f"WheelOdom: dia={self.wheel_d*1000:.1f}mm base={self.wheelbase*1000:.0f}mm")

    def cb(self, msg):
        now = self.get_clock().now()
        dt = (now - self.last_time).nanoseconds * 1e-9
        self.last_time = now
        if dt <= 0 or dt > 0.5:
            return

        vx = msg.linear.x
        vth = msg.angular.z  # full encoder angular, tracked vehicle

        # Integrate (trapezoidal)
        self.x += vx * math.cos(self.yaw + vth * dt / 2.0) * dt
        self.y += vx * math.sin(self.yaw + vth * dt / 2.0) * dt
        self.yaw += vth * dt

        # Publish odometry
        odom = Odometry()
        odom.header.stamp = now.to_msg()
        odom.header.frame_id = self.odom_frame
        odom.child_frame_id = self.base_frame
        odom.pose.pose.position.x = self.x
        odom.pose.pose.position.y = self.y
        q = self._yaw_to_quat(self.yaw)
        odom.pose.pose.orientation = q
        odom.twist.twist = msg
        self.pub.publish(odom)

        # Publish TF
        tf = TransformStamped()
        tf.header.stamp = now.to_msg()
        tf.header.frame_id = self.odom_frame
        tf.child_frame_id = self.base_frame
        tf.transform.translation.x = self.x
        tf.transform.translation.y = self.y
        tf.transform.rotation = q
        self.tf_broadcaster.sendTransform(tf)

    def _yaw_to_quat(self, yaw):
        q = Quaternion()
        q.z = math.sin(yaw / 2.0)
        q.w = math.cos(yaw / 2.0)
        return q


def main():
    rclpy.init()
    rclpy.spin(WheelOdom())
    rclpy.shutdown()

if __name__ == "__main__":
    main()
