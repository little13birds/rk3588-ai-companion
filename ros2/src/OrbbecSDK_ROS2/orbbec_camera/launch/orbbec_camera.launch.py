from launch import LaunchDescription
from launch_ros.actions import Node 
import os
from launch.actions import IncludeLaunchDescription
from launch.conditions import LaunchConfigurationEquals
from launch.launch_description_sources import PythonLaunchDescriptionSource,AnyLaunchDescriptionSource
from ament_index_python.packages import get_package_share_directory
from launch.actions import DeclareLaunchArgument
import os

def generate_launch_description():
	CAMERA_TYPE = os.getenv('CAMERA_TYPE')
	print("my_camera:",CAMERA_TYPE)
	camera_type_arg = DeclareLaunchArgument(name='camera_type', default_value=CAMERA_TYPE, 
                                              description='The type of camera')
	astraproplus_launch = IncludeLaunchDescription(AnyLaunchDescriptionSource(
        [os.path.join(get_package_share_directory('orbbec_camera'), 'launch'),
        '/astra.launch.xml']),
         condition=LaunchConfigurationEquals('camera_type', 'astraproplus')
    )
	gemini2_launch = IncludeLaunchDescription(AnyLaunchDescriptionSource(
        [os.path.join(get_package_share_directory('orbbec_camera'), 'launch'),
        '/gemini2.launch.xml']),
         condition=LaunchConfigurationEquals('camera_type', 'gemini2')
    )
	return LaunchDescription([
        camera_type_arg,
        astraproplus_launch,
        gemini2_launch
    ])
