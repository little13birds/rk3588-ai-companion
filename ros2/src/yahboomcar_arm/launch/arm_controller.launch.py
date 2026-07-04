from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package="yahboomcar_arm",
            executable="arm_controller",
            name="arm_controller",
            output="screen",
        ),
        Node(
            package="yahboomcar_arm",
            executable="camera_bridge",
            name="camera_bridge",
            output="screen",
            parameters=[{
                "url": "http://192.168.176.219:8765/",
                "rate": 20.0,
            }],
        ),
    ])
