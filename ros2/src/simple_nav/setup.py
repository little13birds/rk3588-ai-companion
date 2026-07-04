from setuptools import find_packages, setup
import os
from glob import glob

package_name = "simple_nav"

setup(
    name=package_name,
    version="0.0.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "launch"), glob("launch/*.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="ubuntu",
    maintainer_email="ubuntu@todo.todo",
    description="Bug2 planner + fixed-speed controller",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "simple_navigator = simple_nav.simple_navigator:main",
            "timestamp_bridge = simple_nav.timestamp_bridge:main",
            "wheel_odom = simple_nav.wheel_odom:main",
            "map_publisher = simple_nav.map_publisher:main",
        ],
    },
)
