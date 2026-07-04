"""
Offline map preprocessing only. Requires /map to be published.
Usage: ros2 launch map_preprocess preprocess.launch.py
"""
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package="map_preprocess",
            executable="preprocess_map",
            name="preprocessor",
            output="screen",
        ),
    ])
