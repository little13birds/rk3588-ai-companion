"""Tests for platform depth/RGB camera startup scripts.

Run from repo root:
    python3 -m scripts.test_platform_camera_scripts
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
START_CAMERA = ROOT / "scripts" / "start_platform_camera.sh"
STOP_CAMERA = ROOT / "scripts" / "stop_platform_camera.sh"
SUSPEND_CAMERA = ROOT / "scripts" / "suspend_platform_camera.sh"
RESUME_CAMERA = ROOT / "scripts" / "resume_platform_camera.sh"
START_SYSTEM = ROOT / "scripts" / "start_system.sh"


def _run(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), *args],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, "CLOUD_MODEL_ROOT": str(ROOT)},
        timeout=8.0,
    )


def test_platform_camera_dry_run_names_topics_and_launch():
    assert START_CAMERA.exists(), f"missing script: {START_CAMERA}"
    result = _run(START_CAMERA, "--dry-run")
    assert result.returncode == 0, result.stdout
    assert "ROS_DOMAIN_ID" in START_CAMERA.read_text(encoding="utf-8")
    assert "platform camera" in result.stdout
    assert "orbbec_camera" in result.stdout
    assert "orbbec_camera.launch.py" in result.stdout
    assert "camera_type:=astraproplus" in result.stdout
    assert "enable_ir:=false" in result.stdout
    assert "enable_color:=true" in result.stdout
    assert "enable_depth:=true" in result.stdout
    assert "color_width:=640" in result.stdout
    assert "color_height:=480" in result.stdout
    assert "color_fps:=30" in result.stdout
    assert "depth_width:=640" in result.stdout
    assert "depth_height:=480" in result.stdout
    assert "depth_fps:=30" in result.stdout
    assert "color_fps:=20" not in result.stdout
    assert "depth_fps:=20" not in result.stdout
    assert "/camera/color/image_raw" in result.stdout
    assert "/camera/depth/image_raw" in result.stdout
    assert "not the arm reading camera" in result.stdout


def test_start_system_starts_platform_camera_by_default():
    result = _run(START_SYSTEM, "--dry-run", "--no-main", "--no-audio-fix")
    assert result.returncode == 0, result.stdout
    assert "START_PLATFORM_CAMERA=1" in result.stdout
    assert "start_platform_camera.sh" in result.stdout
    assert "START_READING_ARM=0" in result.stdout
    assert "start_reading_arm.sh" not in result.stdout
    assert "SCHEDULER_AUTO_START_READING_ARM=1" in result.stdout


def test_start_system_can_skip_platform_camera():
    result = _run(START_SYSTEM, "--dry-run", "--no-main", "--no-audio-fix", "--no-platform-camera")
    assert result.returncode == 0, result.stdout
    assert "START_PLATFORM_CAMERA=0" in result.stdout
    assert "start_platform_camera.sh" not in result.stdout


def test_stop_platform_camera_help_is_available():
    assert STOP_CAMERA.exists(), f"missing script: {STOP_CAMERA}"
    result = _run(STOP_CAMERA, "--help")
    assert result.returncode == 0, result.stdout
    assert "platform camera" in result.stdout


def test_stop_platform_camera_has_orbbec_fallback_for_untracked_publishers():
    text = STOP_CAMERA.read_text(encoding="utf-8")
    assert "PLATFORM_CAMERA_STOP_FALLBACK" in text
    assert "fallback_pids()" in text
    assert "orbbec_camera\\.launch\\.py" in text
    assert "orbbec_camera_node" in text
    assert "platform camera pid file not found" in text


def test_start_platform_camera_requires_real_color_frames():
    text = START_CAMERA.read_text(encoding="utf-8")
    assert "PLATFORM_CAMERA_REQUIRE_COLOR_FRAME" in text
    assert "PLATFORM_CAMERA_RESTART_ON_BAD_FRAME" in text
    assert "topic_has_frame()" in text
    assert "ros2 topic echo" in text
    assert "color_frame_ok" in text
    assert "platform camera did not publish required frames" in text
    assert "stop_bad_platform_camera_process" in text
    assert "retrying platform camera start after bad frame health" in text


def test_suspend_and_resume_scripts_call_orbbec_toggle_services():
    assert SUSPEND_CAMERA.exists(), f"missing script: {SUSPEND_CAMERA}"
    assert RESUME_CAMERA.exists(), f"missing script: {RESUME_CAMERA}"
    suspend_text = SUSPEND_CAMERA.read_text(encoding="utf-8")
    resume_text = RESUME_CAMERA.read_text(encoding="utf-8")
    assert "ROS_DOMAIN_ID" in suspend_text
    assert "ROS_DOMAIN_ID" in resume_text
    assert "/camera/toggle_color" in suspend_text
    assert "/camera/toggle_depth" in suspend_text
    assert "{data: ${value}}" in suspend_text
    assert "false" in suspend_text
    assert "Already OFF" in suspend_text
    assert 'PLATFORM_CAMERA_SUSPEND_VERIFY_NO_FRAME="${PLATFORM_CAMERA_SUSPEND_VERIFY_NO_FRAME:-0}"' in suspend_text
    assert "/camera/toggle_depth" in resume_text
    assert "/camera/toggle_color" in resume_text
    assert "{data: ${value}}" in resume_text
    assert "true" in resume_text
    assert "Already ON" in resume_text
    assert 'PLATFORM_CAMERA_RESUME_REQUIRE_DEPTH_FRAME="${PLATFORM_CAMERA_RESUME_REQUIRE_DEPTH_FRAME:-0}"' in resume_text
    assert "ros2 service call" in suspend_text
    assert "ros2 service call" in resume_text


if __name__ == "__main__":
    test_platform_camera_dry_run_names_topics_and_launch()
    print("test_platform_camera_dry_run_names_topics_and_launch PASS")
    test_start_system_starts_platform_camera_by_default()
    print("test_start_system_starts_platform_camera_by_default PASS")
    test_start_system_can_skip_platform_camera()
    print("test_start_system_can_skip_platform_camera PASS")
    test_stop_platform_camera_help_is_available()
    print("test_stop_platform_camera_help_is_available PASS")
    test_stop_platform_camera_has_orbbec_fallback_for_untracked_publishers()
    print("test_stop_platform_camera_has_orbbec_fallback_for_untracked_publishers PASS")
    test_start_platform_camera_requires_real_color_frames()
    print("test_start_platform_camera_requires_real_color_frames PASS")
    test_suspend_and_resume_scripts_call_orbbec_toggle_services()
    print("test_suspend_and_resume_scripts_call_orbbec_toggle_services PASS")
    print("ALL PASS")
