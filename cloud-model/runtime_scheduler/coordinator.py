"""High-level mode coordinator used by main.py."""
from __future__ import annotations

import os
import threading
from typing import Dict, Optional

from .adapters.arm import ArmAgentAdapter
from .adapters.platform_camera import PlatformCameraAdapter
from .adapters.safety import SafetyGuardAdapter
from .modes import NORMAL_POLICY, READING_POLICY
from .scheduler import ResourceScheduler


def _log(event: str, **fields) -> None:
    parts = [f"[scheduler] event={event}"]
    for key, value in fields.items():
        parts.append(f"{key}={value}")
    print(" ".join(parts), flush=True)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


class RuntimeCoordinator:
    def __init__(
        self,
        scheduler: Optional[ResourceScheduler] = None,
        *,
        safety_guard=None,
        arm: Optional[ArmAgentAdapter] = None,
        platform_camera: Optional[PlatformCameraAdapter] = None,
    ):
        self.scheduler = scheduler or ResourceScheduler.from_env()
        self.safety = SafetyGuardAdapter(safety_guard)
        self.arm = arm or ArmAgentAdapter.from_env()
        self.platform_camera = platform_camera or PlatformCameraAdapter.from_env()
        self.reading_pauses_safety = _bool_env("SCHEDULER_READING_PAUSES_SAFETY", True)
        self._lock = threading.RLock()
        self._reading_active = False
        self._reading_resources_held = False
        self._last_reading_health: Dict[str, object] = {}

    @classmethod
    def from_env(cls, *, safety_guard=None) -> "RuntimeCoordinator":
        return cls(ResourceScheduler.from_env(), safety_guard=safety_guard)

    def bootstrap(self) -> None:
        if not self.scheduler.enabled:
            _log("bootstrap_skipped", scheduler_enabled=False)
            return
        result = self.scheduler.acquire_many(
            owner=NORMAL_POLICY.owner,
            mode=NORMAL_POLICY.mode,
            resources=NORMAL_POLICY.resources,
            priority=NORMAL_POLICY.priority,
            reason=NORMAL_POLICY.reason,
        )
        _log(
            "bootstrap_normal",
            ok=result.ok,
            resources=",".join(resource.value for resource in NORMAL_POLICY.resources),
        )

    def start_reading(self) -> bool:
        if not self.scheduler.enabled:
            _log("reading_start_requested", scheduler_enabled=False)
            prepared = self.arm.prepare_reading()
            _log("arm_prepare_ok" if prepared else "arm_prepare_failed", endpoint="/reading/prepare")
            if not prepared:
                return False
            ok = self.arm.start_reading()
            _log("arm_start_ok" if ok else "arm_start_failed", endpoint="/reading/start")
            return bool(ok)
        with self._lock:
            reuse_reading_resources = self._reading_resources_held
            _log(
                "reading_start_requested",
                scheduler_enabled=True,
                pauses_safety=self.reading_pauses_safety,
                stop_platform_camera=getattr(self.platform_camera, "enabled", True),
                platform_camera_release_mode=getattr(self.platform_camera, "release_mode", "stop"),
                auto_start_arm=self.arm.auto_start,
                reuse_resources=reuse_reading_resources,
            )
            result = self.scheduler.acquire_many(
                owner=READING_POLICY.owner,
                mode=READING_POLICY.mode,
                resources=READING_POLICY.resources,
                priority=READING_POLICY.priority,
                reason=READING_POLICY.reason,
                preempt=READING_POLICY.preempt,
            )
            if not result.ok:
                _log("resource_conflict", mode="reading", conflicts=result.conflicts)
                return False
            _log(
                "resources_acquired",
                mode="reading",
                resources=",".join(resource.value for resource in READING_POLICY.resources),
            )
            if reuse_reading_resources:
                _log("reading_resources_reused", platform_camera_released=True, safety_paused=self.reading_pauses_safety)
            if self.reading_pauses_safety and not reuse_reading_resources:
                self.safety.pause("reading")
                _log("safety_paused", reason="reading")
            elif self.reading_pauses_safety:
                _log("safety_pause_skipped", reason="reading_already_paused")
            if not reuse_reading_resources:
                _log("platform_camera_release_begin", reason="reading", enabled=getattr(self.platform_camera, "enabled", True))
                if not self.platform_camera.release_for_reading("reading"):
                    _log("platform_camera_release_failed", reason="reading")
                    self._restore_normal_after_reading_failure("platform_camera_stop_failed")
                    return False
                self._reading_resources_held = True
                _log("platform_camera_release_ok", reason="reading")
            else:
                _log("platform_camera_release_skipped", reason="already_released")
            _log("arm_health_check_begin", mode="reading", require_frame=self.arm.require_frame)
            health = self.arm.ensure_running()
            self._last_reading_health = health
            if not health.get("ok"):
                _log("arm_unhealthy", mode="reading", health=health)
                self._restore_normal_after_reading_failure("reading_start_failed")
                return False
            _log("arm_health_ok", mode="reading", health=health)
            if reuse_reading_resources:
                _log("arm_prepare_skipped", reason="next_page_reuse")
            else:
                _log("arm_prepare_begin", endpoint="/reading/prepare")
                prepared = self.arm.prepare_reading()
                if not prepared:
                    _log("arm_prepare_failed", endpoint="/reading/prepare")
                    self._restore_normal_after_reading_failure("reading_prepare_failed")
                    return False
                _log("arm_prepare_ok", endpoint="/reading/prepare")
            _log("arm_start_begin", endpoint="/reading/start")
            ok = self.arm.start_reading()
            self._reading_active = bool(ok)
            if not ok:
                _log("arm_start_failed", endpoint="/reading/start")
                self._restore_normal_after_reading_failure("reading_start_failed")
                return False
            _log("arm_start_ok", endpoint="/reading/start")
            _log("reading_started")
            return ok

    def stop_reading(self, return_home: bool = False) -> bool:
        _log("reading_stop_requested", return_home=bool(return_home), scheduler_enabled=self.scheduler.enabled)
        ok = self.arm.stop_reading(return_home=return_home)
        _log("arm_stop_ok" if ok else "arm_stop_failed", return_home=bool(return_home))
        if not self.scheduler.enabled:
            return ok
        with self._lock:
            self._reading_active = False
            self._reading_resources_held = False
            self.scheduler.release_owner(READING_POLICY.owner)
            _log("resources_released", owner=READING_POLICY.owner)
            _log("arm_service_stop_begin", reason="reading_stop")
            if self.arm.stop_service("reading_stop"):
                _log("arm_service_stop_ok", reason="reading_stop")
            else:
                _log("arm_service_stop_failed", reason="reading_stop")
            _log("platform_camera_restore_begin", reason="reading_stop", enabled=getattr(self.platform_camera, "enabled", True))
            if self.platform_camera.restore_after_reading("reading_stop"):
                _log("platform_camera_restore_ok", reason="reading_stop")
            else:
                _log("platform_camera_restore_failed", reason="reading_stop")
            if self.reading_pauses_safety:
                self.safety.resume("reading_stop")
                _log("safety_resumed", reason="reading_stop")
            result = self.scheduler.acquire_many(
                owner=NORMAL_POLICY.owner,
                mode=NORMAL_POLICY.mode,
                resources=NORMAL_POLICY.resources,
                priority=NORMAL_POLICY.priority,
                reason=NORMAL_POLICY.reason,
            )
            _log("normal_restored", reason="reading_stop", ok=result.ok)
        return ok

    def pause_reading_page(self) -> bool:
        """Pause tracking between pages without restoring normal camera resources."""
        _log("reading_page_pause_requested", scheduler_enabled=self.scheduler.enabled)
        ok = self.arm.stop_reading(return_home=False)
        _log("arm_stop_ok" if ok else "arm_stop_failed", return_home=False, page_pause=True)
        self._reading_active = False
        if not self.scheduler.enabled:
            return ok
        _log("reading_page_paused", keep_resources=True, platform_camera_restored=False)
        return ok

    def _restore_normal_after_reading_failure(self, reason: str) -> None:
        _log("restore_normal_begin", reason=reason)
        self._reading_active = False
        self._reading_resources_held = False
        self.scheduler.release_owner(READING_POLICY.owner)
        _log("resources_released", owner=READING_POLICY.owner)
        _log("arm_service_stop_begin", reason=reason)
        if self.arm.stop_service(reason):
            _log("arm_service_stop_ok", reason=reason)
        else:
            _log("arm_service_stop_failed", reason=reason)
        _log("platform_camera_restore_begin", reason=reason, enabled=getattr(self.platform_camera, "enabled", True))
        if self.platform_camera.restore_after_reading(reason):
            _log("platform_camera_restore_ok", reason=reason)
        else:
            _log("platform_camera_restore_failed", reason=reason)
        if self.reading_pauses_safety:
            self.safety.resume(reason)
            _log("safety_resumed", reason=reason)
        result = self.scheduler.acquire_many(
            owner=NORMAL_POLICY.owner,
            mode=NORMAL_POLICY.mode,
            resources=NORMAL_POLICY.resources,
            priority=NORMAL_POLICY.priority,
            reason=NORMAL_POLICY.reason,
        )
        _log("normal_restored", reason=reason, ok=result.ok)

    def shutdown(self) -> None:
        if self._reading_active:
            self.stop_reading()

    def snapshot(self) -> Dict[str, object]:
        data = self.scheduler.snapshot()
        data["reading"] = {
            "active": self._reading_active,
            "resources_held": self._reading_resources_held,
            "last_health": dict(self._last_reading_health),
            "auto_start": self.arm.auto_start,
        }
        data["safety_guard"] = self.safety.status()
        data["platform_camera"] = self.platform_camera.status()
        data["config"] = {
            "reading_pauses_safety": self.reading_pauses_safety,
            "scheduler_enabled_env": os.environ.get("RESOURCE_SCHEDULER_ENABLED", "1"),
        }
        return data
