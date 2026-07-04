from launch import LaunchDescription
from launch.substitutions import EnvironmentVariable
import launch.actions
import launch_ros.actions
from launch_ros.actions import Node
import os
import launch
from ament_index_python.packages import get_package_share_directory

def generate_launch_description():
    use_sim_time = launch.substitutions.LaunchConfiguration('use_sim_time', default='false')
    return LaunchDescription([

        Node(
            package='slam_gmapping',
            namespace='transform',
            executable='transform',
            output='screen',
            parameters=[{'parents_frame': "odom",
                         'child_frame': "laser",
                         'x': 0.1,
                         'y': 0.2,
                         'z': 0.5,
                         'roll': 0.0,
                         'pitch': 0.0,
                         'yaw': 0.0}],
        ),
        Node(
            package='tf2_ros',
            namespace='laser_to_base_link',
            executable='static_transform_publisher',
            output='screen',
            arguments = [ '0', 
            			  '0', 
            			 '1', 
            			 '0', 
            			  '0', 
            			  '0', 
            			  'laser', 
            			 'base_link']

        ),
        
        Node(
            package='rviz2',
            namespace='map_rviz',
            executable='rviz2',
            output='screen',
            arguments = [ '-d', "/home/yahboom/rplidar_ws/src/rplidar_ros-dev-ros2/rviz/sllidar_gmapping_ros2.rviz"],
        )
      
    ])
