"""PlatformCameraAdapter tests.

Run from repo root:
    python3 -m runtime_scheduler.test_platform_camera_adapter
"""
from __future__ import annotations

import os
from types import SimpleNamespace

from runtime_scheduler.adapters import platform_camera


def test_from_env_uses_safe_script_timeout_default() -> None:
    old_value = os.environ.pop("SCHEDULER_PLATFORM_CAMERA_SCRIPT_TIMEOUT_SEC", None)
    try:
        adapter = platform_camera.PlatformCameraAdapter.from_env()
    finally:
        if old_value is not None:
            os.environ["SCHEDULER_PLATFORM_CAMERA_SCRIPT_TIMEOUT_SEC"] = old_value
    assert adapter.script_timeout_sec >= 40.0, adapter


def test_run_passes_configured_timeout_to_subprocess() -> None:
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="ok\n")

    old_run = platform_camera.subprocess.run
    try:
        platform_camera.subprocess.run = fake_run
        adapter = platform_camera.PlatformCameraAdapter(
            start_script="/tmp/start_platform_camera.sh",
            stop_script="/tmp/stop_platform_camera.sh",
            script_timeout_sec=55.0,
        )
        assert adapter.start("test") is True
    finally:
        platform_camera.subprocess.run = old_run

    assert calls, calls
    assert calls[0][1]["timeout"] == 55.0, calls


def test_suspend_mode_uses_suspend_and_resume_scripts() -> None:
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="ok\n")

    old_run = platform_camera.subprocess.run
    try:
        platform_camera.subprocess.run = fake_run
        adapter = platform_camera.PlatformCameraAdapter(
            start_script="/tmp/start_platform_camera.sh",
            stop_script="/tmp/stop_platform_camera.sh",
            suspend_script="/tmp/suspend_platform_camera.sh",
            resume_script="/tmp/resume_platform_camera.sh",
            release_mode="suspend",
            script_timeout_sec=12.0,
        )
        assert adapter.release_for_reading("reading") is True
        assert adapter.restore_after_reading("reading_stop") is True
    finally:
        platform_camera.subprocess.run = old_run

    scripts = [call[0][0][0] for call in calls]
    assert scripts == ["/tmp/suspend_platform_camera.sh", "/tmp/resume_platform_camera.sh"], scripts


def test_suspend_failure_falls_back_to_stop_mode() -> None:
    calls = []

    def fake_run(*args, **kwargs):
        script = args[0][0]
        calls.append(script)
        if script.endswith("suspend_platform_camera.sh"):
            return SimpleNamespace(returncode=1, stdout="service failed\n")
        return SimpleNamespace(returncode=0, stdout="ok\n")

    old_run = platform_camera.subprocess.run
    try:
        platform_camera.subprocess.run = fake_run
        adapter = platform_camera.PlatformCameraAdapter(
            start_script="/tmp/start_platform_camera.sh",
            stop_script="/tmp/stop_platform_camera.sh",
            suspend_script="/tmp/suspend_platform_camera.sh",
            resume_script="/tmp/resume_platform_camera.sh",
            release_mode="suspend",
            fallback_to_stop=True,
            script_timeout_sec=12.0,
        )
        assert adapter.release_for_reading("reading") is True
    finally:
        platform_camera.subprocess.run = old_run

    assert calls == ["/tmp/suspend_platform_camera.sh", "/tmp/stop_platform_camera.sh"], calls


def test_from_env_can_select_suspend_mode() -> None:
    old_values = {
        key: os.environ.get(key)
        for key in (
            "SCHEDULER_PLATFORM_CAMERA_RELEASE_MODE",
            "PLATFORM_CAMERA_SUSPEND_SCRIPT",
            "PLATFORM_CAMERA_RESUME_SCRIPT",
            "SCHEDULER_PLATFORM_CAMERA_SUSPEND_FALLBACK_TO_STOP",
        )
    }
    try:
        os.environ["SCHEDULER_PLATFORM_CAMERA_RELEASE_MODE"] = "suspend"
        os.environ["PLATFORM_CAMERA_SUSPEND_SCRIPT"] = "/tmp/suspend.sh"
        os.environ["PLATFORM_CAMERA_RESUME_SCRIPT"] = "/tmp/resume.sh"
        os.environ["SCHEDULER_PLATFORM_CAMERA_SUSPEND_FALLBACK_TO_STOP"] = "0"
        adapter = platform_camera.PlatformCameraAdapter.from_env()
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert adapter.release_mode == "suspend"
    assert adapter.suspend_script == "/tmp/suspend.sh"
    assert adapter.resume_script == "/tmp/resume.sh"
    assert adapter.fallback_to_stop is False


if __name__ == "__main__":
    test_from_env_uses_safe_script_timeout_default()
    print("test_from_env_uses_safe_script_timeout_default PASS")
    test_run_passes_configured_timeout_to_subprocess()
    print("test_run_passes_configured_timeout_to_subprocess PASS")
    test_suspend_mode_uses_suspend_and_resume_scripts()
    print("test_suspend_mode_uses_suspend_and_resume_scripts PASS")
    test_suspend_failure_falls_back_to_stop_mode()
    print("test_suspend_failure_falls_back_to_stop_mode PASS")
    test_from_env_can_select_suspend_mode()
    print("test_from_env_can_select_suspend_mode PASS")
    print("ALL PASS")
