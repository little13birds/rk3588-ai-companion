from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    args = [
        DeclareLaunchArgument('velocity_topic', default_value='/vel_raw'),
        DeclareLaunchArgument('imu_topic', default_value='/imu/data_raw'),
        DeclareLaunchArgument('odom_topic', default_value='/odom_combined'),
        DeclareLaunchArgument('status_topic', default_value='/depth_camera/fused_pose_status'),
        DeclareLaunchArgument('web_host', default_value='0.0.0.0'),
        DeclareLaunchArgument('web_port', default_value='8091'),
        DeclareLaunchArgument('publish_period_s', default_value='0.02'),
        DeclareLaunchArgument('velocity_timeout_s', default_value='0.30'),
        DeclareLaunchArgument('imu_timeout_s', default_value='0.30'),
        DeclareLaunchArgument('imu_weight', default_value='0.80'),
        DeclareLaunchArgument('imu_yaw_rate_sign', default_value='1.0'),
        DeclareLaunchArgument('linear_x_scale', default_value='0.9'),
        DeclareLaunchArgument('yaw_rate_scale', default_value='1.53'),
        DeclareLaunchArgument('frame_id', default_value='odom_combined'),
        DeclareLaunchArgument('child_frame_id', default_value='base_link'),
    ]

    node = Node(
        package='depth_camera_perception',
        executable='fused_pose_monitor',
        name='fused_pose_monitor',
        output='screen',
        parameters=[
            {
                'velocity_topic': LaunchConfiguration('velocity_topic'),
                'imu_topic': LaunchConfiguration('imu_topic'),
                'odom_topic': LaunchConfiguration('odom_topic'),
                'status_topic': LaunchConfiguration('status_topic'),
                'web_host': LaunchConfiguration('web_host'),
                'web_port': LaunchConfiguration('web_port'),
                'publish_period_s': LaunchConfiguration('publish_period_s'),
                'velocity_timeout_s': LaunchConfiguration('velocity_timeout_s'),
                'imu_timeout_s': LaunchConfiguration('imu_timeout_s'),
                'imu_weight': LaunchConfiguration('imu_weight'),
                'imu_yaw_rate_sign': LaunchConfiguration('imu_yaw_rate_sign'),
                'linear_x_scale': LaunchConfiguration('linear_x_scale'),
                'yaw_rate_scale': LaunchConfiguration('yaw_rate_scale'),
                'frame_id': LaunchConfiguration('frame_id'),
                'child_frame_id': LaunchConfiguration('child_frame_id'),
            }
        ],
    )

    return LaunchDescription(args + [node])
