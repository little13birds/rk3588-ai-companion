#!/bin/bash
conda deactivate

# 设置 ROS2 环境

# 加载 ROS2 基础环境
source /opt/ros/humble/setup.bash

# 加载当前工作空间的环境
source ~/ros2/install/setup.bash

# 可选：添加一些常用别名或路径
# alias ros2_build='colcon build --symlink-install'
# export ROS_DOMAIN_ID=42

echo "ROS2 Humble environment sourced. Working space: ~/ros2"