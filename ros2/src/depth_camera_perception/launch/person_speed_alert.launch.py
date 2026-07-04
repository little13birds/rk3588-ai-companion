from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription(
        [
            DeclareLaunchArgument("color_topic", default_value="/camera/color/image_raw"),
            DeclareLaunchArgument("depth_topic", default_value="/camera/depth/image_raw"),
            DeclareLaunchArgument("camera_info_topic", default_value="/camera/color/camera_info"),
            DeclareLaunchArgument("alert_topic", default_value="/depth_camera/person_speed_alert"),
            DeclareLaunchArgument("detector_backend", default_value="ultralytics"),
            DeclareLaunchArgument("model_path", default_value="/home/elf/ros2/yolov8n.pt"),
            DeclareLaunchArgument("confidence", default_value="0.4"),
            DeclareLaunchArgument("speed_threshold_mps", default_value="1.5"),
            DeclareLaunchArgument("duration_threshold_s", default_value="1.0"),
            DeclareLaunchArgument("alert_cooldown_s", default_value="5.0"),
            Node(
                package="depth_camera_perception",
                executable="person_speed_alert",
                output="screen",
                parameters=[
                    {
                        "color_topic": LaunchConfiguration("color_topic"),
                        "depth_topic": LaunchConfiguration("depth_topic"),
                        "camera_info_topic": LaunchConfiguration("camera_info_topic"),
                        "alert_topic": LaunchConfiguration("alert_topic"),
                        "detector_backend": LaunchConfiguration("detector_backend"),
                        "model_path": LaunchConfiguration("model_path"),
                        "confidence": LaunchConfiguration("confidence"),
                        "speed_threshold_mps": LaunchConfiguration("speed_threshold_mps"),
                        "duration_threshold_s": LaunchConfiguration("duration_threshold_s"),
                        "alert_cooldown_s": LaunchConfiguration("alert_cooldown_s"),
                    }
                ],
            ),
        ]
    )
