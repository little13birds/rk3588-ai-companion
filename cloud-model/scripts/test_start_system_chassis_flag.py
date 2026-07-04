"""Start script chassis flag tests.

Run from repo root:
    python3 -m scripts.test_start_system_chassis_flag
"""
from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
START_SYSTEM = ROOT / "scripts" / "start_system.sh"


def _run_start_system(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(START_SYSTEM), *args],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def test_default_start_enables_dashboard_chassis_control() -> None:
    result = _run_start_system(
        "--dry-run",
        "--no-main",
        "--no-audio-fix",
        "--no-platform-camera",
    )
    assert result.returncode == 0, result.stdout
    assert "DASHBOARD_CHASSIS_CONTROL_ENABLED=1" in result.stdout
    print("test_default_start_enables_dashboard_chassis_control PASS")


def test_help_does_not_require_extra_chassis_flag() -> None:
    result = _run_start_system("--help")
    assert result.returncode == 0, result.stdout
    assert "--with-chassis" not in result.stdout
    print("test_help_does_not_require_extra_chassis_flag PASS")


if __name__ == "__main__":
    test_default_start_enables_dashboard_chassis_control()
    test_help_does_not_require_extra_chassis_flag()
    print("ALL PASS")
