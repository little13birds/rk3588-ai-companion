"""
Cartographer SLAM with Laser + Depth Fusion
Usage: ros2 launch yahboomcar_nav2 cartographer_slam_launch.py
"""
import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.actions import IncludeLaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    pkg_dir = get_package_share_directory('yahboomcar_nav2')
    rf2o_dir = get_package_share_directory('rf2o_laser_odometry')
    orbbec_dir = get_package_share_directory('orbbec_camera')

    # --- Launch Arguments ---
    use_sim_time = LaunchConfiguration('use_sim_time', default='false')
    configuration_directory = os.path.join(pkg_dir, 'params')
    configuration_basename = 'lds_2d_with_depth.lua'
    resolution = LaunchConfiguration('resolution', default='0.05')
    publish_period_sec = LaunchConfiguration('publish_period_sec', default='1.0')

    # --- 1. RPLIDAR A1 ---
    rplidar_node = Node(
        package='rplidar_ros',
        executable='rplidar_node',
        name='rplidar_node',
        parameters=[{
            'channel_type': 'serial',
            'serial_port': '/dev/ttyUSB1',
            'serial_baudrate': 115200,
            'frame_id': 'laser',
            'inverted': False,
            'angle_compensate': True,
            'scan_mode': 'Standard',
        }],
        output='screen'
    )

    # --- 2. Astra Pro Plus Depth Camera ---
    camera_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(orbbec_dir, 'launch', 'orbbec_camera.launch.py')
        ),
        launch_arguments={
            'camera_type': 'astraproplus',
            'enable_depth': 'true',
            'depth_width': '640',
            'depth_height': '480',
            'depth_fps': '30',
            'depth_format': 'Y11',
            'enable_point_cloud': 'true',
            'enable_colored_point_cloud': 'false',
            'enable_color': 'false',
            'enable_ir': 'false',
            'publish_tf': 'true',
        }.items()
    )

    # --- 3. rf2o Laser Odometry ---
    rf2o_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(rf2o_dir, 'launch', 'rf2o_laser_odometry.launch.py')
        ),
        launch_arguments={
            'laser_scan_topic': '/scan',
        }.items()
    )

    # --- 4. Static TF: base_link -> laser ---
    tf_base_to_laser = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='base_to_laser',
        arguments=[
            '--x', '0.08',
            '--y', '0.0',
            '--z', '0.15',
            '--roll', '0',
            '--pitch', '0',
            '--yaw', '3.14159',
            '--frame-id', 'base_link',
            '--child-frame-id', 'laser'
        ]
    )

    # --- 5. Static TF: base_link -> camera_link ---
    tf_base_to_camera = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='base_to_camera',
        arguments=[
            '--x', '0.10',
            '--y', '0.0',
            '--z', '0.12',
            '--roll', '0',
            '--pitch', '0',
            '--yaw', '0',
            '--frame-id', 'base_link',
            '--child-frame-id', 'camera_link'
        ]
    )

    # --- 6. Cartographer SLAM Node ---
    cartographer_node = Node(
        package='cartographer_ros',
        executable='cartographer_node',
        name='cartographer_node',
        output='screen',
        parameters=[{'use_sim_time': use_sim_time}],
        arguments=[
            '-configuration_directory', configuration_directory,
            '-configuration_basename', configuration_basename,
        ],
        remappings=[
            ('scan', '/scan'),
            ('points2', '/camera/depth/points'),
            ('odom', '/odom_rf2o'),
        ],
    )

    # --- 7. Occupancy Grid Node ---
    occupancy_grid_node = Node(
        package='cartographer_ros',
        executable='cartographer_occupancy_grid_node',
        name='occupancy_grid_node',
        output='screen',
        parameters=[{'use_sim_time': use_sim_time}],
        arguments=[
            '-resolution', resolution,
            '-publish_period_sec', publish_period_sec,
        ],
    )

    return LaunchDescription([
        DeclareLaunchArgument('use_sim_time', default_value='false'),
        DeclareLaunchArgument('resolution', default_value='0.05'),
        DeclareLaunchArgument('publish_period_sec', default_value='1.0'),
        rplidar_node,
        camera_launch,
        rf2o_launch,
        tf_base_to_laser,
        tf_base_to_camera,
        cartographer_node,
        occupancy_grid_node,
    ])
