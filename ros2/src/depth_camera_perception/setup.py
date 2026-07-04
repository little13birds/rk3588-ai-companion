from glob import glob

from setuptools import find_packages, setup

package_name = "depth_camera_perception"

setup(
    name=package_name,
    version="0.0.1",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
        (f"share/{package_name}/launch", glob("launch/*.launch.py")),
        (f"share/{package_name}/config", glob("config/*.yaml")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="elf",
    maintainer_email="elf@example.com",
    description="Depth camera perception smoke tests for person detection and distance estimation.",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "perception_smoke_test = depth_camera_perception.perception_smoke_test:main",
            "obstacle_guard = depth_camera_perception.obstacle_guard_node:main",
            "obstacle_web_monitor = depth_camera_perception.obstacle_web_monitor_node:main",
            "person_speed_alert = depth_camera_perception.person_speed_alert_node:main",
            "person_web_monitor = depth_camera_perception.person_web_monitor_node:main",
            "person_seek = depth_camera_perception.person_seek_node:main",
            "person_follow = depth_camera_perception.person_follow_node:main",
            "fused_pose_monitor = depth_camera_perception.fused_pose_monitor_node:main",
        ],
    },
)
