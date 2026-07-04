"""Adapter for the platform Orbbec/Astra camera scripts."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Dict


def _log(event: str, **fields) -> None:
    parts = [f"[scheduler.platform_camera] event={event}"]
    for key, value in fields.items():
        parts.append(f"{key}={value}")
    print(" ".join(parts), flush=True)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass
class PlatformCameraAdapter:
    start_script: str = "scripts/start_platform_camera.sh"
    stop_script: str = "scripts/stop_platform_camera.sh"
    suspend_script: str = "scripts/suspend_platform_camera.sh"
    resume_script: str = "scripts/resume_platform_camera.sh"
    enabled: bool = True
    release_mode: str = "stop"
    fallback_to_stop: bool = True
    script_timeout_sec: float = 45.0

    @classmethod
    def from_env(cls) -> "PlatformCameraAdapter":
        return cls(
            start_script=os.environ.get("PLATFORM_CAMERA_START_SCRIPT", "scripts/start_platform_camera.sh"),
            stop_script=os.environ.get("PLATFORM_CAMERA_STOP_SCRIPT", "scripts/stop_platform_camera.sh"),
            suspend_script=os.environ.get("PLATFORM_CAMERA_SUSPEND_SCRIPT", "scripts/suspend_platform_camera.sh"),
            resume_script=os.environ.get("PLATFORM_CAMERA_RESUME_SCRIPT", "scripts/resume_platform_camera.sh"),
            enabled=_bool_env("SCHEDULER_READING_STOPS_PLATFORM_CAMERA", True),
            release_mode=os.environ.get("SCHEDULER_PLATFORM_CAMERA_RELEASE_MODE", "stop").strip().lower() or "stop",
            fallback_to_stop=_bool_env("SCHEDULER_PLATFORM_CAMERA_SUSPEND_FALLBACK_TO_STOP", True),
            script_timeout_sec=_float_env("SCHEDULER_PLATFORM_CAMERA_SCRIPT_TIMEOUT_SEC", 45.0),
        )

    def stop(self, reason: str = "") -> bool:
        if not self.enabled:
            _log("stop_skipped", reason=reason, enabled=False)
            return True
        return self._run(self.stop_script, "stop", reason, self.script_timeout_sec)

    def start(self, reason: str = "") -> bool:
        if not self.enabled:
            _log("start_skipped", reason=reason, enabled=False)
            return True
        return self._run(self.start_script, "start", reason, self.script_timeout_sec)

    def release_for_reading(self, reason: str = "") -> bool:
        if not self.enabled:
            _log("release_skipped", reason=reason, enabled=False)
            return True
        if self.release_mode == "suspend":
            ok = self._run(self.suspend_script, "suspend", reason, self.script_timeout_sec)
            if ok:
                return True
            if self.fallback_to_stop:
                _log("suspend_fallback_to_stop", reason=reason)
                return self.stop(reason)
            return False
        return self.stop(reason)

    def restore_after_reading(self, reason: str = "") -> bool:
        if not self.enabled:
            _log("restore_skipped", reason=reason, enabled=False)
            return True
        if self.release_mode == "suspend":
            ok = self._run(self.resume_script, "resume", reason, self.script_timeout_sec)
            if ok:
                return True
            if self.fallback_to_stop:
                _log("resume_fallback_to_start", reason=reason)
                return self.start(reason)
            return False
        return self.start(reason)

    def status(self) -> Dict[str, object]:
        return {
            "enabled": self.enabled,
            "start_script": self.start_script,
            "stop_script": self.stop_script,
            "suspend_script": self.suspend_script,
            "resume_script": self.resume_script,
            "release_mode": self.release_mode,
            "fallback_to_stop": self.fallback_to_stop,
            "script_timeout_sec": self.script_timeout_sec,
        }

    @staticmethod
    def _run(script_path: str, action: str, reason: str, timeout_sec: float) -> bool:
        script = os.path.expanduser(script_path)
        _log(f"{action}_begin", reason=reason, script=script)
        try:
            result = subprocess.run(
                [script],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=max(1.0, float(timeout_sec)),
            )
        except Exception as exc:
            _log(f"{action}_failed", reason=reason, error_type=type(exc).__name__, error=exc)
            return False
        tail = "\n".join(result.stdout.splitlines()[-8:])
        if result.returncode != 0:
            _log(f"{action}_failed", reason=reason, returncode=result.returncode, output_tail=repr(tail))
            return False
        _log(f"{action}_done", reason=reason, returncode=result.returncode, output_tail=repr(tail))
        return True
