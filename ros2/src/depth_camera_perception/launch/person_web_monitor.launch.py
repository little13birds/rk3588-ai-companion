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
            DeclareLaunchArgument("detector_backend", default_value="ultralytics"),
            DeclareLaunchArgument("model_path", default_value="/home/elf/ros2/yolov8n.pt"),
            DeclareLaunchArgument("confidence", default_value="0.4"),
            DeclareLaunchArgument("roi_fraction", default_value="0.5"),
            DeclareLaunchArgument("speed_threshold_mps", default_value="1.5"),
            DeclareLaunchArgument("duration_threshold_s", default_value="1.0"),
            DeclareLaunchArgument("alert_cooldown_s", default_value="5.0"),
            DeclareLaunchArgument("max_sample_gap_s", default_value="0.75"),
            DeclareLaunchArgument("process_period_sec", default_value="0.2"),
            DeclareLaunchArgument("log_period_sec", default_value="1.0"),
            DeclareLaunchArgument("web_host", default_value="0.0.0.0"),
            DeclareLaunchArgument("web_port", default_value="8088"),
            DeclareLaunchArgument("jpeg_quality", default_value="80"),
            DeclareLaunchArgument("stream_period_sec", default_value="0.2"),
            DeclareLaunchArgument("alert_hold_sec", default_value="1.5"),
            Node(
                package="depth_camera_perception",
                executable="person_web_monitor",
                output="screen",
                parameters=[
                    {
                        "color_topic": LaunchConfiguration("color_topic"),
                        "depth_topic": LaunchConfiguration("depth_topic"),
                        "camera_info_topic": LaunchConfiguration("camera_info_topic"),
                        "detector_backend": LaunchConfiguration("detector_backend"),
                        "model_path": LaunchConfiguration("model_path"),
                        "confidence": LaunchConfiguration("confidence"),
                        "roi_fraction": LaunchConfiguration("roi_fraction"),
                        "speed_threshold_mps": LaunchConfiguration("speed_threshold_mps"),
                        "duration_threshold_s": LaunchConfiguration("duration_threshold_s"),
                        "alert_cooldown_s": LaunchConfiguration("alert_cooldown_s"),
                        "max_sample_gap_s": LaunchConfiguration("max_sample_gap_s"),
                        "process_period_sec": LaunchConfiguration("process_period_sec"),
                        "log_period_sec": LaunchConfiguration("log_period_sec"),
                        "web_host": LaunchConfiguration("web_host"),
                        "web_port": LaunchConfiguration("web_port"),
                        "jpeg_quality": LaunchConfiguration("jpeg_quality"),
                        "stream_period_sec": LaunchConfiguration("stream_period_sec"),
                        "alert_hold_sec": LaunchConfiguration("alert_hold_sec"),
                    }
                ],
            ),
        ]
    )
