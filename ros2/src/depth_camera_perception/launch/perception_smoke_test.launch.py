from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription(
        [
            DeclareLaunchArgument("color_topic", default_value="/camera/color/image_raw"),
            DeclareLaunchArgument("depth_topic", default_value="/camera/depth/image_raw"),
            DeclareLaunchArgument("detector_backend", default_value="ultralytics"),
            DeclareLaunchArgument("model_path", default_value="yolov8n.pt"),
            DeclareLaunchArgument("confidence", default_value="0.4"),
            Node(
                package="depth_camera_perception",
                executable="perception_smoke_test",
                output="screen",
                parameters=[
                    {
                        "color_topic": LaunchConfiguration("color_topic"),
                        "depth_topic": LaunchConfiguration("depth_topic"),
                        "detector_backend": LaunchConfiguration("detector_backend"),
                        "model_path": LaunchConfiguration("model_path"),
                        "confidence": LaunchConfiguration("confidence"),
                    }
                ],
            ),
        ]
    )
