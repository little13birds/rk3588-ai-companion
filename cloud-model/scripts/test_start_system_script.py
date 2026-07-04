"""Tests for the quick system startup shell script.

Run from the repo root:
    python3 -m scripts.test_start_system_script
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "start_system.sh"


def _run(*args: str, env_overrides=None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "CLOUD_MODEL_ROOT": str(ROOT)}
    env.pop("SCHEDULER_AUTO_START_READING_ARM", None)
    env.pop("PLATFORM_CAMERA_LAUNCH_CMD", None)
    env.pop("ROS_DOMAIN_ID", None)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        timeout=8.0,
    )


def test_dry_run_prints_effective_start_plan():
    assert SCRIPT.exists(), f"missing script: {SCRIPT}"
    with tempfile.TemporaryDirectory() as tmp:
        fake_arm = Path(tmp) / "start_reading_arm.sh"
        fake_arm.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        fake_arm.chmod(0o755)
        result = _run(
            "--dry-run",
            "--no-main",
            "--no-audio-fix",
            "--with-arm",
            "--no-dashboard",
            "--no-safety",
            "--no-scheduler",
            env_overrides={"READING_ARM_START_SCRIPT": str(fake_arm)},
        )
    assert result.returncode == 0, result.stdout
    assert "START_READING_ARM=1" in result.stdout
    assert "SAFETY_GUARD_ENABLED=0" in result.stdout
    assert "DASHBOARD_ENABLED=0" in result.stdout
    assert "RESOURCE_SCHEDULER_ENABLED=0" in result.stdout
    assert "SCHEDULER_READING_STOPS_PLATFORM_CAMERA=1" in result.stdout
    assert "setup.bash" in result.stdout
    assert "start_reading_arm.sh" in result.stdout
    assert "reading arm prepare capability" in result.stdout
    assert "python3 main.py" in result.stdout


def test_default_runtime_scheduler_auto_starts_reading_arm_on_demand():
    result = _run("--dry-run", "--no-main", "--no-audio-fix")
    assert result.returncode == 0, result.stdout
    assert "START_READING_ARM=0" in result.stdout
    assert "ROS_DOMAIN_ID=30" in result.stdout
    assert "SCHEDULER_AUTO_START_READING_ARM=1" in result.stdout
    assert "SCHEDULER_PLATFORM_CAMERA_RELEASE_MODE=suspend" in result.stdout
    assert "start_reading_arm.sh" not in result.stdout


def test_platform_camera_release_mode_can_be_overridden_to_stop():
    result = _run(
        "--dry-run",
        "--no-main",
        "--no-audio-fix",
        env_overrides={"SCHEDULER_PLATFORM_CAMERA_RELEASE_MODE": "stop"},
    )
    assert result.returncode == 0, result.stdout
    assert "SCHEDULER_PLATFORM_CAMERA_RELEASE_MODE=stop" in result.stdout


def test_auto_start_reading_arm_can_be_disabled():
    result = _run("--dry-run", "--no-main", "--no-audio-fix", "--no-auto-start-arm")
    assert result.returncode == 0, result.stdout
    assert "SCHEDULER_AUTO_START_READING_ARM=0" in result.stdout
    assert "START_READING_ARM=0" in result.stdout


def test_cli_debug_uses_same_startup_but_runs_debug_runtime():
    with tempfile.TemporaryDirectory() as tmp:
        fake_stop = Path(tmp) / "stop_reading_arm.sh"
        fake_stop.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        fake_stop.chmod(0o755)
        result = _run(
            "--dry-run",
            "--no-main",
            "--no-audio-fix",
            "--cli-debug",
            env_overrides={"READING_ARM_STOP_SCRIPT": str(fake_stop)},
        )
    assert result.returncode == 0, result.stdout
    assert "START_PLATFORM_CAMERA=1" in result.stdout
    assert "CLI_DEBUG=1" in result.stdout
    assert "SCHEDULER_AUTO_START_READING_ARM=1" in result.stdout
    assert "stop stale reading arm before platform camera startup" in result.stdout
    assert "start_platform_camera.sh" in result.stdout
    assert "would run: python3 debug_runtime.py" in result.stdout


def test_dialog_debug_skips_hardware_and_runs_dialog_runtime():
    result = _run("--dry-run", "--no-main", "--dialog-debug")

    assert result.returncode == 0, result.stdout
    assert "DIALOG_DEBUG=1" in result.stdout
    assert "START_PLATFORM_CAMERA=0" in result.stdout
    assert "SAFETY_GUARD_ENABLED=0" in result.stdout
    assert "DASHBOARD_ENABLED=0" in result.stdout
    assert "RESOURCE_SCHEDULER_ENABLED=0" in result.stdout
    assert "start_platform_camera.sh" not in result.stdout
    assert "fix_audio" not in result.stdout
    assert "would run: python3 dialog_debug.py" in result.stdout


def test_voice_dialog_debug_keeps_audio_but_skips_robot_hardware():
    result = _run("--dry-run", "--no-main", "--no-audio-fix", "--voice-dialog-debug")

    assert result.returncode == 0, result.stdout
    assert "VOICE_DIALOG_DEBUG=1" in result.stdout
    assert "DIALOG_DEBUG=0" in result.stdout
    assert "START_PLATFORM_CAMERA=0" in result.stdout
    assert "SAFETY_GUARD_ENABLED=0" in result.stdout
    assert "DASHBOARD_ENABLED=0" in result.stdout
    assert "RESOURCE_SCHEDULER_ENABLED=0" in result.stdout
    assert "start_platform_camera.sh" not in result.stdout
    assert "setup.bash" not in result.stdout
    assert "would run: python3 voice_dialog_debug.py" in result.stdout


def test_default_start_releases_stale_reading_arm_before_platform_camera():
    with tempfile.TemporaryDirectory() as tmp:
        fake_stop = Path(tmp) / "stop_reading_arm.sh"
        fake_stop.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        fake_stop.chmod(0o755)
        result = _run(
            "--dry-run",
            "--no-main",
            "--no-audio-fix",
            env_overrides={"READING_ARM_STOP_SCRIPT": str(fake_stop)},
        )
    assert result.returncode == 0, result.stdout
    stop_idx = result.stdout.index("stop stale reading arm before platform camera startup")
    platform_idx = result.stdout.index("start_platform_camera.sh")
    assert stop_idx < platform_idx, result.stdout


def test_with_arm_keeps_explicit_reading_arm_start_path():
    result = _run("--dry-run", "--no-main", "--no-audio-fix", "--with-arm")
    assert result.returncode == 0, result.stdout
    assert "START_READING_ARM=1" in result.stdout
    assert "stop stale reading arm before platform camera startup" not in result.stdout



def test_start_script_checks_reading_arm_prepare_capability():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "check_reading_arm_prepare_capability" in text
    assert "/reading/prepare?timeout=0.5" in text
    assert "prepare_complete" in text

def test_help_is_available():
    result = _run("--help")
    assert result.returncode == 0, result.stdout
    assert "Usage:" in result.stdout
    assert "--with-arm" in result.stdout
    assert "--cli-debug" in result.stdout
    assert "--dialog-debug" in result.stdout
    assert "--voice-dialog-debug" in result.stdout
    assert "--no-auto-start-arm" in result.stdout
    assert "--dry-run" in result.stdout


def test_start_script_checks_orphan_main_processes():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "find_main_pids()" in text
    assert "check_existing_main_processes" in text
    assert "residual cloud-model process" in text
    assert "stop_residual_main_processes" in text


def test_stop_timeout_is_configurable_and_long_enough_for_cleanup():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "CLOUD_MODEL_STOP_TIMEOUT_SEC" in text
    assert "STOP_TIMEOUT_SEC" in text
    assert "15" in text
    assert "did not exit after ${STOP_TIMEOUT_SEC}s" in text


def test_stop_uses_sigint_before_sigterm_for_python_cleanup():
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'kill -INT "${pid}"' in text
    assert "sending INT for graceful cleanup" in text


if __name__ == "__main__":
    test_dry_run_prints_effective_start_plan()
    print("test_dry_run_prints_effective_start_plan PASS")
    test_default_runtime_scheduler_auto_starts_reading_arm_on_demand()
    print("test_default_runtime_scheduler_auto_starts_reading_arm_on_demand PASS")
    test_platform_camera_release_mode_can_be_overridden_to_stop()
    print("test_platform_camera_release_mode_can_be_overridden_to_stop PASS")
    test_auto_start_reading_arm_can_be_disabled()
    print("test_auto_start_reading_arm_can_be_disabled PASS")
    test_cli_debug_uses_same_startup_but_runs_debug_runtime()
    print("test_cli_debug_uses_same_startup_but_runs_debug_runtime PASS")
    test_dialog_debug_skips_hardware_and_runs_dialog_runtime()
    print("test_dialog_debug_skips_hardware_and_runs_dialog_runtime PASS")
    test_voice_dialog_debug_keeps_audio_but_skips_robot_hardware()
    print("test_voice_dialog_debug_keeps_audio_but_skips_robot_hardware PASS")
    test_default_start_releases_stale_reading_arm_before_platform_camera()
    print("test_default_start_releases_stale_reading_arm_before_platform_camera PASS")
    test_with_arm_keeps_explicit_reading_arm_start_path()
    print("test_with_arm_keeps_explicit_reading_arm_start_path PASS")
    test_start_script_checks_reading_arm_prepare_capability()
    print("test_start_script_checks_reading_arm_prepare_capability PASS")
    test_help_is_available()
    print("test_help_is_available PASS")
    test_start_script_checks_orphan_main_processes()
    print("test_start_script_checks_orphan_main_processes PASS")
    test_stop_timeout_is_configurable_and_long_enough_for_cleanup()
    print("test_stop_timeout_is_configurable_and_long_enough_for_cleanup PASS")
    test_stop_uses_sigint_before_sigterm_for_python_cleanup()
    print("test_stop_uses_sigint_before_sigterm_for_python_cleanup PASS")
    print("ALL PASS")
