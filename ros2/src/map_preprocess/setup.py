from setuptools import find_packages, setup
import os
from glob import glob

package_name = "map_preprocess"

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
    description="Map preprocessing: obstacle bbox + waypoint graph",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "preprocess_map = map_preprocess.preprocessor:main",
            "waypoint_navigator = map_preprocess.waypoint_navigator:main",
        ],
    },
)
