"""
GMapping SLAM Launch (All-in-one)
Usage: ros2 launch yahboomcar_nav2 gmapping_slam_launch.py
"""
import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node


def generate_launch_description():
    bringup_dir = get_package_share_directory('yahboomcar_bringup')
    rplidar_dir = get_package_share_directory('rplidar_ros')
    rf2o_dir = get_package_share_directory('rf2o_laser_odometry')
    slam_dir = get_package_share_directory('yahboomcar_slam')

    # --- 1. Chassis + IMU + EKF ---
    bringup_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(bringup_dir, 'launch', 'yahboomcar_bringup_X1_launch.py')
        )
    )

    # --- 2. RPLIDAR A1 (override serial_port and scan_mode) ---
    rplidar_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(rplidar_dir, 'launch', 'rplidar_a1_launch.py')
        ),
        launch_arguments={
            'serial_port': '/dev/ttyUSB1',
            'scan_mode': 'Standard',
        }.items()
    )

    # --- 3. rf2o Laser Odometry (official, includes init_pose_from_topic: '') ---
    rf2o_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(rf2o_dir, 'launch', 'rf2o_laser_odometry.launch.py')
        ),
        launch_arguments={
            'laser_scan_topic': '/scan',
        }.items()
    )

    # --- 4. GMapping SLAM ---
    gmapping_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(slam_dir, 'launch', 'gmapping_only_launch.py')
        )
    )

    # --- 5. Static TF: base_link -> laser ---
    tf_base_to_laser = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='base_to_laser',
        arguments=[
            '--x', '0.08', '--y', '0.0', '--z', '0.15',
            '--roll', '0', '--pitch', '0', '--yaw', '0.0',
            '--frame-id', 'base_link',
            '--child-frame-id', 'laser'
        ]
    )

    return LaunchDescription([
        bringup_launch,
        rplidar_launch,
        rf2o_launch,
        gmapping_launch,
        tf_base_to_laser,
    ])
