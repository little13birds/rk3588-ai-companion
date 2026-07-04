from setuptools import setup
import os
from glob import glob

package_name = "yahboomcar_arm"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "launch"), glob("launch/*.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="ubuntu",
    maintainer_email="ubuntu@todo.todo",
    description="Yahboom X3 5-DOF Arm Visual Servoing Controller",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "arm_controller = yahboomcar_arm.arm_controller:main",
            "camera_bridge = yahboomcar_arm.camera_bridge:main",
            "test_publisher = yahboomcar_arm.test_publisher:main",
        ],
    },
)
