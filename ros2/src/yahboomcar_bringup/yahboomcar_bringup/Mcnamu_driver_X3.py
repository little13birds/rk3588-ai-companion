#!/usr/bin/env python
# encoding: utf-8

import sys
import math
import random
import threading
from math import pi
from time import sleep
from Rosmaster_Lib import Rosmaster
from .serial_watchdog import RosmasterWatchdog

import rclpy
from rclpy.node import Node
from std_msgs.msg import String,Float32,Int32,Bool
from geometry_msgs.msg import Twist
from sensor_msgs.msg import Imu,MagneticField, JointState
from rclpy.clock import Clock

class yahboomcar_driver(Node):
    def __init__(self, name):
        super().__init__(name)
        self.RA2DE = 180 / pi
        self.wd = RosmasterWatchdog(com="/dev/myserial", usb_port="1-1.2.2", max_fail=5)
        self.wd.connect()
        self.car = self.wd.car

        self.car.set_car_type(1)

        self.declare_parameter('imu_link', 'imu_link')
        self.imu_link = self.get_parameter('imu_link').get_parameter_value().string_value
        self.declare_parameter('Prefix', "")
        self.Prefix = self.get_parameter('Prefix').get_parameter_value().string_value

        #create subcriber
        self.sub_cmd_vel = self.create_subscription(Twist,"cmd_vel",self.cmd_vel_callback,1)
        self.sub_RGBLight = self.create_subscription(Int32,"RGBLight",self.RGBLightcallback,100)
        self.sub_BUzzer = self.create_subscription(Bool,"Buzzer",self.Buzzercallback,100)

        #create publisher
        self.EdiPublisher = self.create_publisher(Float32,"edition",100)
        self.volPublisher = self.create_publisher(Float32,"voltage",100)
        self.staPublisher = self.create_publisher(JointState,"joint_states",100)
        self.velPublisher = self.create_publisher(Twist,"vel_raw",50)
        self.imuPublisher = self.create_publisher(Imu,"/imu/data_raw",100)
        self.magPublisher = self.create_publisher(MagneticField,"/imu/mag",100)

        #create timer
        self.timer = self.create_timer(0.1, self.pub_data)
        self.edition = Float32()
        self.edition.data = 1.0
        self.car.create_receive_threading()

    def cmd_vel_callback(self,msg):
        if not isinstance(msg, Twist): return

        mcu_joy_x_strafe = 0.0
        mcu_joy_y_forward = msg.linear.x * 1.0
        mcu_joy_z_turn = -msg.angular.z * 1.0

        self.car.set_car_motion(mcu_joy_x_strafe, mcu_joy_y_forward, mcu_joy_z_turn)

    def RGBLightcallback(self,msg):
        if not isinstance(msg, Int32): return
        for i in range(3): self.car.set_colorful_effect(msg.data, 6, parm=1)

    def Buzzercallback(self,msg):
        if not isinstance(msg, Bool): return
        if msg.data:
            for i in range(3): self.car.set_beep(1)
        else:
            for i in range(3): self.car.set_beep(0)

    def pub_data(self):
        time_stamp = Clock().now()
        imu = Imu()
        twist = Twist()
        battery = Float32()
        edition = Float32()
        mag = MagneticField()
        state = JointState()
        state.header.stamp = time_stamp.to_msg()
        state.header.frame_id = "joint_states"
        if len(self.Prefix)==0:
            state.name = ["back_right_joint", "back_left_joint","front_left_steer_joint","front_left_wheel_joint",
                            "front_right_steer_joint","front_right_wheel_joint"]
        else:
            state.name = [self.Prefix+"back_right_joint",self.Prefix+ "back_left_joint",self.Prefix+"front_left_steer_joint",self.Prefix+"front_left_wheel_joint",
                            self.Prefix+"front_right_steer_joint", self.Prefix+"front_right_wheel_joint"]

        edition.data = self.car.get_version()*1.0
        battery.data = self.car.get_battery_voltage()*1.0
        ax, ay, az = self.car.get_accelerometer_data()
        gx, gy, gz = self.car.get_gyroscope_data()
        mx, my, mz = self.car.get_magnetometer_data()

        mcu_joy_x_strafe, mcu_joy_y_forward, mcu_joy_z_turn = self.car.get_motion_data()

        imu.header.stamp = time_stamp.to_msg()
        imu.header.frame_id = self.imu_link
        imu.linear_acceleration.x = ax*1.0
        imu.linear_acceleration.y = ay*1.0
        imu.linear_acceleration.z = az*1.0
        imu.angular_velocity.x = gx*1.0
        imu.angular_velocity.y = gy*1.0
        imu.angular_velocity.z = gz*1.0
        # covariance for robot_localization EKF
        imu.angular_velocity_covariance = [0.01, 0.0, 0.0,
                                            0.0, 0.01, 0.0,
                                            0.0, 0.0, 0.01]
        imu.linear_acceleration_covariance = [0.1, 0.0, 0.0,
                                               0.0, 0.1, 0.0,
                                               0.0, 0.0, 0.1]

        mag.header.stamp = time_stamp.to_msg()
        mag.header.frame_id = self.imu_link
        mag.magnetic_field.x = mx*1.0
        mag.magnetic_field.y = my*1.0
        mag.magnetic_field.z = mz*1.0

        twist.linear.x = mcu_joy_y_forward * 1.0
        twist.linear.y = 0.0
        twist.angular.z = -mcu_joy_z_turn * 1.0

        self.velPublisher.publish(twist)
        self.imuPublisher.publish(imu)
        self.magPublisher.publish(mag)
        self.volPublisher.publish(battery)
        self.EdiPublisher.publish(edition)

def main():
    rclpy.init()
    driver = yahboomcar_driver('driver_node')
    rclpy.spin(driver)

if __name__ == '__main__':
    main()
