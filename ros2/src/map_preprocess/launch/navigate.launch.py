"""
Full navigation: Cartographer localization + chassis + waypoint navigator
Usage: ros2 launch map_preprocess navigate.launch.py
"""
import os
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("use_sim_time", default_value="False"),

        # 1. Chassis
        Node(
            package="yahboomcar_bringup",
            executable="Mcnamu_driver_X3",
            name="driver_node",
            output="screen",
        ),
        # 2. RPLIDAR
        Node(
            package="rplidar_ros",
            executable="rplidar_node",
            name="rplidar",
            parameters=[{
                "serial_port": "/dev/ttyUSB1",
                "serial_baudrate": 115200,
                "frame_id": "laser",
                "angle_compensate": True,
                "scan_mode": "Standard",
            }],
            output="screen",
        ),
        # 3. Static TF base_link -> laser
        Node(
            package="tf2_ros",
            executable="static_transform_publisher",
            name="base_to_laser",
            arguments=[
                "--x", "0.08", "--y", "0.0", "--z", "0.15",
                "--roll", "0", "--pitch", "0", "--yaw", "0.0",
                "--frame-id", "base_link", "--child-frame-id", "laser",
            ],
        ),
        # 4. Cartographer
        Node(
            package="cartographer_ros",
            executable="cartographer_node",
            name="cartographer_node",
            output="screen",
            parameters=[{"use_sim_time": LaunchConfiguration("use_sim_time")}],
            arguments=[
                "-configuration_directory", os.path.expanduser("~/carto_config"),
                "-configuration_basename", "test_localization.lua",
                "-load_state_filename", os.path.expanduser("~/maps/my_room.pbstream"),
            ],
        ),
        # 5. Occupancy grid /map
        Node(
            package="cartographer_ros",
            executable="cartographer_occupancy_grid_node",
            name="occupancy_grid_node",
            parameters=[{"use_sim_time": LaunchConfiguration("use_sim_time"),
                         "resolution": 0.05}],
        ),
        # 6. Waypoint navigator
        Node(
            package="map_preprocess",
            executable="waypoint_navigator",
            name="waypoint_navigator",
            output="screen",
        ),
    ])
