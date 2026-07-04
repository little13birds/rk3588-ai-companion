"""Adapter around ROS-side arm_agent and reading-arm scripts."""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Dict

from arm import agent_client


def _log(event: str, **fields) -> None:
    parts = [f"[scheduler.arm] event={event}"]
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
class ArmAgentAdapter:
    start_script: str = "~/ros2/start_reading_arm.sh"
    stop_script: str = "~/ros2/stop_reading_arm.sh"
    auto_start: bool = False
    require_frame: bool = True
    start_health_wait_sec: float = 8.0
    start_health_poll_sec: float = 0.5
    return_home_settle_sec: float = 3.0

    @classmethod
    def from_env(cls) -> "ArmAgentAdapter":
        return cls(
            start_script=os.environ.get("READING_ARM_START_SCRIPT", "~/ros2/start_reading_arm.sh"),
            stop_script=os.environ.get("READING_ARM_STOP_SCRIPT", "~/ros2/stop_reading_arm.sh"),
            auto_start=_bool_env("SCHEDULER_AUTO_START_READING_ARM", False),
            require_frame=_bool_env("SCHEDULER_REQUIRE_FRAME_HEALTH", True),
            start_health_wait_sec=_float_env("SCHEDULER_ARM_START_HEALTH_WAIT_SEC", 8.0),
            start_health_poll_sec=_float_env("SCHEDULER_ARM_START_HEALTH_POLL_SEC", 0.5),
            return_home_settle_sec=_float_env("SCHEDULER_ARM_RETURN_HOME_SETTLE_SEC", 3.0),
        )

    def health(self, require_frame: bool | None = None) -> Dict[str, object]:
        return agent_client.health(
            require_frame=self.require_frame if require_frame is None else bool(require_frame),
            timeout=1.5,
        )

    def ensure_running(self) -> Dict[str, object]:
        _log("health_check_begin", require_frame=self.require_frame)
        health = self.health()
        if health.get("ok"):
            _log("health_ok", auto_start=False, health=health)
            return health
        if not self.auto_start:
            health["auto_start"] = False
            _log("health_failed", auto_start=False, health=health)
            return health
        script = os.path.expanduser(self.start_script)
        _log("auto_start_begin", script=script)
        try:
            result = subprocess.run(
                [script],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=25.0,
            )
        except Exception as exc:
            _log("auto_start_failed", script=script, error_type=type(exc).__name__, error=exc)
            return {"ok": False, "error": f"start_failed:{type(exc).__name__}", "detail": str(exc)}
        health = self.health()
        attempts = 1
        health["auto_start"] = True
        health["start_returncode"] = result.returncode
        health["start_output_tail"] = "\n".join(result.stdout.splitlines()[-8:])
        deadline = time.monotonic() + max(0.0, float(self.start_health_wait_sec))
        poll_sec = max(0.0, float(self.start_health_poll_sec))
        if not health.get("ok") and result.returncode == 0 and self.start_health_wait_sec > 0:
            _log(
                "auto_start_wait_begin",
                wait_sec=self.start_health_wait_sec,
                poll_sec=poll_sec,
                first_health=health,
            )
        while not health.get("ok") and result.returncode == 0 and time.monotonic() < deadline:
            if poll_sec > 0:
                time.sleep(poll_sec)
            attempts += 1
            health = self.health()
            health["auto_start"] = True
            health["start_returncode"] = result.returncode
            health["start_output_tail"] = "\n".join(result.stdout.splitlines()[-8:])
            health["health_attempts"] = attempts
            _log("auto_start_wait_retry", attempt=attempts, health_ok=bool(health.get("ok")), health=health)
        health["health_attempts"] = attempts
        _log(
            "auto_start_done",
            script=script,
            returncode=result.returncode,
            health_ok=bool(health.get("ok")),
            attempts=attempts,
            output_tail=repr(health["start_output_tail"]),
        )
        return health

    def prepare_reading(self) -> bool:
        ok = agent_client.prepare_reading()
        _log("prepare_done", ok=ok)
        return ok

    def start_reading(self) -> bool:
        ok = agent_client.start_reading()
        _log("start_done", ok=ok)
        return ok

    def stop_reading(self, return_home: bool = False) -> bool:
        ok = agent_client.stop_reading(return_home=return_home)
        _log("stop_done", ok=ok, return_home=bool(return_home))
        if ok and return_home and self.return_home_settle_sec > 0:
            _log("return_home_wait_begin", wait_sec=self.return_home_settle_sec)
            time.sleep(float(self.return_home_settle_sec))
            _log("return_home_wait_done", wait_sec=self.return_home_settle_sec)
        return ok

    def stop_service(self, reason: str = "") -> bool:
        script = os.path.expanduser(self.stop_script)
        _log("service_stop_begin", reason=reason, script=script)
        try:
            result = subprocess.run(
                [script],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=20.0,
            )
        except Exception as exc:
            _log("service_stop_failed", reason=reason, error_type=type(exc).__name__, error=exc)
            return False
        tail = "\n".join(result.stdout.splitlines()[-8:])
        if result.returncode != 0:
            _log("service_stop_failed", reason=reason, returncode=result.returncode, output_tail=repr(tail))
            return False
        _log("service_stop_done", reason=reason, returncode=result.returncode, output_tail=repr(tail))
        return True
