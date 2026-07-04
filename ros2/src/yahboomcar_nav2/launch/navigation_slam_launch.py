"""
Navigation Launch (rf2o odometry, no EKF)
Usage: ros2 launch yahboomcar_nav2 navigation_slam_launch.py map:=$HOME/maps/my_room.yaml
"""
import os
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.substitutions import LaunchConfiguration
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    nav2_bringup_dir = get_package_share_directory("nav2_bringup")
    pkg_dir = get_package_share_directory("yahboomcar_nav2")
    rf2o_dir = get_package_share_directory("rf2o_laser_odometry")

    map_file = LaunchConfiguration("map")
    params_file = os.path.join(pkg_dir, "config", "nav2_params.yaml")

    return LaunchDescription([

        DeclareLaunchArgument("map",
            default_value=os.path.expanduser("~/maps/my_room.yaml")),
        DeclareLaunchArgument("params_file", default_value=params_file),

        # --- 1. Chassis Driver ---
        Node(
            package="yahboomcar_bringup",
            executable="Mcnamu_driver_X3",
            name="driver_node",
            output="screen"
        ),

        # --- 2. RPLIDAR A1 ---
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
            output="screen"
        ),

        # --- 3. RF2O Laser Odometry (publishes odom->base_link TF) ---
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(rf2o_dir, "launch", "rf2o_laser_odometry.launch.py")
            ),
            launch_arguments={"laser_scan_topic": "/scan"}.items()
        ),

        # --- 4. Static TF: base_link -> laser ---
        Node(
            package="tf2_ros",
            executable="static_transform_publisher",
            name="base_to_laser",
            arguments=[
                "--x", "0.08", "--y", "0.0", "--z", "0.15",
                "--roll", "0", "--pitch", "0", "--yaw", "0.0",
                "--frame-id", "base_link",
                "--child-frame-id", "laser"
            ]
        ),

        # --- 5. Nav2 Bringup ---
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(nav2_bringup_dir, "launch", "bringup_launch.py")
            ),
            launch_arguments={
                "map": map_file,
                "use_sim_time": "False",
                "params_file": params_file,
                "use_composition": "False",
            }.items()
        ),
    ])
