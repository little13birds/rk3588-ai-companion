from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    args = [
        DeclareLaunchArgument('image_topic', default_value='/camera/color/image_raw'),
        DeclareLaunchArgument('depth_topic', default_value='/camera/depth/image_raw'),
        DeclareLaunchArgument('status_topic', default_value='/depth_camera/obstacle_status'),
        DeclareLaunchArgument('image_encoding', default_value='bgr8'),
        DeclareLaunchArgument('web_host', default_value='0.0.0.0'),
        DeclareLaunchArgument('web_port', default_value='8090'),
        DeclareLaunchArgument('jpeg_quality', default_value='80'),
        DeclareLaunchArgument('stream_period_sec', default_value='0.2'),
        DeclareLaunchArgument('process_period_sec', default_value='0.1'),
    ]

    node = Node(
        package='depth_camera_perception',
        executable='obstacle_web_monitor',
        name='obstacle_web_monitor',
        output='screen',
        parameters=[
            {
                'image_topic': LaunchConfiguration('image_topic'),
                'depth_topic': LaunchConfiguration('depth_topic'),
                'status_topic': LaunchConfiguration('status_topic'),
                'image_encoding': LaunchConfiguration('image_encoding'),
                'web_host': LaunchConfiguration('web_host'),
                'web_port': LaunchConfiguration('web_port'),
                'jpeg_quality': LaunchConfiguration('jpeg_quality'),
                'stream_period_sec': LaunchConfiguration('stream_period_sec'),
                'process_period_sec': LaunchConfiguration('process_period_sec'),
            }
        ],
    )

    return LaunchDescription(args + [node])
