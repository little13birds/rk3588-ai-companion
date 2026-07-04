from setuptools import setup, find_packages
import os
from glob import glob

package_name = 'face_track'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    entry_points={
        'console_scripts': [
            'face_detector = face_track.face_detector:main',
            'book_detector = face_track.book_detector:main',
            'servo_controller = face_track.servo_controller:main',
            'keyboard_face_info = face_track.keyboard_face_info:main',
            'keyboard_joint_jog = face_track.keyboard_face_info:main',
            'keyboard_direction_jog = face_track.keyboard_direction_jog:main',
            'book_servo_bridge = face_track.book_servo_bridge:main',
            'arm_agent = face_track.arm_agent:main',
        ],
    },
)
