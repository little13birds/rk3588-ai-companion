import os
from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg_dir = get_package_share_directory('face_track')
    params_file = os.path.join(pkg_dir, 'config', 'servo_params.yaml')

    return LaunchDescription([
        # ---- USB 摄像头 ----
        Node(
            package='usb_cam',
            executable='usb_cam_node_exe',
            name='usb_cam',
            parameters=[{
                'video_device': '/dev/video21',
                'image_width': 640,
                'image_height': 480,
                'pixel_format': 'MJPG',
                'framerate': 30.0,
            }],
            remappings=[('/image_raw', '/image_raw')],
        ),

        # ---- 人脸检测 ----
        Node(
            package='face_track',
            executable='face_detector',
            name='face_detector',
            parameters=[params_file],
        ),

        # ---- 伺服控制器 ----
        Node(
            package='face_track',
            executable='servo_controller',
            name='servo_controller',
            parameters=[params_file],
        ),
    ])
