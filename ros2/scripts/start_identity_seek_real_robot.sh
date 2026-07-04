#!/usr/bin/env bash
set -eo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${LOG_DIR:-/tmp}"
TARGET_NAME="${TARGET_NAME:-tao}"
WEB_PORT="${WEB_PORT:-8092}"
ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-30}"
START_CAMERA="${START_CAMERA:-1}"

source /opt/ros/humble/setup.bash
source "$ROOT_DIR/install/setup.bash"
set -euo pipefail
export ROS_DOMAIN_ID

echo "[identity_seek] root=$ROOT_DIR"
echo "[identity_seek] target=$TARGET_NAME web=http://0.0.0.0:$WEB_PORT"

pid_matches() {
  local pattern="$1"
  ps -eo pid,args | awk -v pat="$pattern" '$0 ~ pat {print $1}'
}

stop_matches() {
  local label="$1"
  local pattern="$2"
  local pids
  pids="$(pid_matches "$pattern" || true)"
  if [[ -n "$pids" ]]; then
    echo "[identity_seek] stopping stale $label: $pids"
    # shellcheck disable=SC2086
    kill $pids || true
    sleep 0.5
  fi
}

is_running() {
  local pattern="$1"
  [[ -n "$(pid_matches "$pattern" || true)" ]]
}

start_bg() {
  local label="$1"
  local logfile="$2"
  shift 2
  echo "[identity_seek] starting $label log=$logfile"
  (cd "$ROOT_DIR" && setsid -f nohup "$@" > "$logfile" 2>&1 </dev/null)
}

if [[ "$START_CAMERA" == "1" ]]; then
  if ! is_running "[o]rbbec_camera_node"; then
    start_bg "orbbec_camera" "$LOG_DIR/orbbec_identity_test.log" \
      ros2 launch orbbec_camera orbbec_camera.launch.py camera_type:=astraproplus
    sleep 3
  else
    echo "[identity_seek] orbbec_camera already running"
  fi
fi

if ! is_running "[M]cnamu_driver_X3"; then
  start_bg "Mcnamu_driver_X3" "$LOG_DIR/mcnamu_driver_identity_test.log" \
    ros2 run yahboomcar_bringup Mcnamu_driver_X3
  sleep 1
else
  echo "[identity_seek] Mcnamu_driver_X3 already running"
fi

if ! is_running "[b]ase_node_X3"; then
  start_bg "base_node_X3" "$LOG_DIR/base_node_identity_test.log" \
    ros2 run yahboomcar_base_node base_node_X3 --ros-args -p pub_odom_tf:=false
  sleep 1
else
  echo "[identity_seek] base_node_X3 already running"
fi

if ! is_running "[f]used_pose_monitor"; then
  start_bg "fused_pose_monitor" "$LOG_DIR/fused_pose_identity_test.log" \
    ros2 launch depth_camera_perception fused_pose_monitor.launch.py
  sleep 1
else
  echo "[identity_seek] fused_pose_monitor already running"
fi

stop_matches "person_seek" "[p]erson_seek.launch.py|[d]epth_camera_perception/person_seek"
stop_matches "obstacle_guard" "[o]bstacle_guard.launch.py|[d]epth_obstacle_guard"

start_bg "obstacle_guard" "$LOG_DIR/obstacle_guard_identity_test.log" \
  ros2 launch depth_camera_perception obstacle_guard.launch.py \
    dry_run:=false \
    allow_bypass:=true \
    use_fused_pose_bypass:=true \
    input_cmd_vel_topic:=/cmd_vel_raw \
    output_cmd_vel_topic:=/cmd_vel \
    front_invalid_depth_block_fraction:=0.90 \
    side_invalid_depth_block_fraction:=0.90 \
    normal_forward_mps:=0.40 \
    bypass_forward_mps:=0.20 \
    return_heading_angular_sign:=-1.0 \
    avoid_min_forward_s:=1.00 \
    side_clear_hold_s:=1.00 \
    exit_forward_hold_s:=0.50
sleep 2

start_bg "person_seek" "$LOG_DIR/person_seek_identity_test.log" \
  ros2 launch depth_camera_perception person_seek.launch.py \
    mode:=identity \
    target_name:="$TARGET_NAME" \
    detector_backend:=pose_rknn \
    model_path:=/home/elf/face_identity_rk3588/models/rknn/pose_yolov8n_hybrid.rknn \
    auto_start:=true \
    web_port:="$WEB_PORT" \
    search_angular_z:=${SEARCH_ANGULAR_Z:-0.20} \
    approach_max_forward_mps:=${APPROACH_MAX_FORWARD_MPS:-0.25} \
    approach_slow_forward_mps:=${APPROACH_SLOW_FORWARD_MPS:-0.08} \
    approach_max_angular_z:=${APPROACH_MAX_ANGULAR_Z:-0.20}
sleep 2

echo "[identity_seek] topic chain:"
ros2 topic info /cmd_vel_raw || true
ros2 topic info /cmd_vel || true
ros2 topic info /vel_raw || true
ros2 topic info /odom_raw || true

echo "[identity_seek] ready"
echo "[identity_seek] person seek ui: http://$(hostname -I | awk '{print $1}'):$WEB_PORT"
echo "[identity_seek] obstacle ui: start obstacle_web_monitor.launch.py web_port:=8090 if needed"
