from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "start_identity_seek_real_robot.sh"


def test_identity_seek_real_robot_script_starts_complete_chassis_chain():
    text = SCRIPT.read_text()

    assert "Mcnamu_driver_X3" in text
    assert "obstacle_guard.launch.py" in text
    assert "person_seek.launch.py" in text
    assert "input_cmd_vel_topic:=/cmd_vel_raw" in text
    assert "output_cmd_vel_topic:=/cmd_vel" in text


def test_identity_seek_real_robot_script_uses_conservative_motion_defaults():
    text = SCRIPT.read_text()

    assert "search_angular_z:=${SEARCH_ANGULAR_Z:-0.20}" in text
    assert "approach_max_forward_mps:=${APPROACH_MAX_FORWARD_MPS:-0.25}" in text
    assert "approach_slow_forward_mps:=${APPROACH_SLOW_FORWARD_MPS:-0.08}" in text


def test_identity_seek_real_robot_script_sources_ros_before_nounset():
    lines = SCRIPT.read_text().splitlines()
    strict_line = lines.index("set -euo pipefail")
    ros_source_line = lines.index("source /opt/ros/humble/setup.bash")

    assert ros_source_line < strict_line


def test_identity_seek_real_robot_script_detaches_background_processes():
    text = SCRIPT.read_text()

    assert "setsid -f nohup" in text
    assert "</dev/null" in text
