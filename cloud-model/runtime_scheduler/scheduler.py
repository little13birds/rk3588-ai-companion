"""Small in-process resource scheduler.

The first version is deliberately deterministic and local-only. It does not
start ROS processes itself; adapters decide what side effects to run after a
lease is granted.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from .leases import ResourceLease
from .resources import Resource, resource_info


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


@dataclass
class AcquireResult:
    ok: bool
    leases: List[ResourceLease] = field(default_factory=list)
    conflicts: List[Dict[str, object]] = field(default_factory=list)
    preempted: List[ResourceLease] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "ok": self.ok,
            "leases": [lease.to_dict() for lease in self.leases],
            "conflicts": list(self.conflicts),
            "preempted": [lease.to_dict() for lease in self.preempted],
        }


class ResourceScheduler:
    def __init__(self, enabled: bool = True):
        self.enabled = bool(enabled)
        self._lock = threading.RLock()
        self._leases: List[ResourceLease] = []
        self._mode = "normal"
        self._last_conflicts: List[Dict[str, object]] = []
        self._history: List[Dict[str, object]] = []

    @classmethod
    def from_env(cls) -> "ResourceScheduler":
        return cls(enabled=_bool_env("RESOURCE_SCHEDULER_ENABLED", True))

    def set_mode(self, mode: str) -> None:
        with self._lock:
            if mode != self._mode:
                self._mode = mode
                self._history.append({"event": "mode", "mode": mode, "at": time.time()})
                self._history = self._history[-80:]

    def acquire_many(
        self,
        *,
        owner: str,
        mode: str,
        resources: Iterable[Resource],
        priority: int,
        reason: str = "",
        preempt: bool = False,
        preemptible: bool = True,
        ttl_sec: Optional[float] = None,
    ) -> AcquireResult:
        if not self.enabled:
            return AcquireResult(ok=True)
        requested = [Resource(item) for item in resources]
        with self._lock:
            self._drop_expired_locked()
            conflicts: List[Dict[str, object]] = []
            preempted: List[ResourceLease] = []

            for resource in requested:
                for lease in self._conflicting_leases_locked(resource, owner):
                    if preempt and lease.preemptible and lease.priority < priority:
                        if lease not in preempted:
                            preempted.append(lease)
                        continue
                    conflicts.append({
                        "resource": resource.value,
                        "owner": owner,
                        "blocked_by": lease.owner,
                        "blocked_resource": lease.resource.value,
                        "blocked_mode": lease.mode,
                        "reason": reason,
                    })

            if conflicts:
                self._last_conflicts = conflicts[-20:]
                self._history.append({"event": "conflict", "conflicts": conflicts, "at": time.time()})
                self._history = self._history[-80:]
                return AcquireResult(ok=False, conflicts=conflicts, preempted=preempted)

            for lease in preempted:
                if lease in self._leases:
                    self._leases.remove(lease)

            granted = []
            for resource in requested:
                existing = self._find_owner_lease_locked(resource, owner)
                if existing:
                    existing.refresh()
                    existing.mode = mode
                    existing.priority = priority
                    existing.reason = reason
                    granted.append(existing)
                    continue
                lease = ResourceLease(
                    resource=resource,
                    owner=owner,
                    mode=mode,
                    priority=priority,
                    reason=reason,
                    preemptible=preemptible,
                    ttl_sec=ttl_sec,
                )
                self._leases.append(lease)
                granted.append(lease)

            self._mode = mode
            self._last_conflicts = []
            self._history.append({
                "event": "acquire",
                "owner": owner,
                "mode": mode,
                "resources": [r.value for r in requested],
                "preempted": [p.owner for p in preempted],
                "at": time.time(),
            })
            self._history = self._history[-80:]
            return AcquireResult(ok=True, leases=granted, preempted=preempted)

    def release_owner(self, owner: str) -> int:
        if not self.enabled:
            return 0
        with self._lock:
            before = len(self._leases)
            self._leases = [lease for lease in self._leases if lease.owner != owner]
            released = before - len(self._leases)
            if released:
                self._history.append({"event": "release", "owner": owner, "count": released, "at": time.time()})
                self._history = self._history[-80:]
            return released

    def release_mode(self, mode: str) -> int:
        if not self.enabled:
            return 0
        with self._lock:
            before = len(self._leases)
            self._leases = [lease for lease in self._leases if lease.mode != mode]
            return before - len(self._leases)

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            self._drop_expired_locked()
            resources = {}
            for resource in Resource:
                resources[resource.value] = {
                    "description": resource_info(resource).description,
                    "leases": [lease.to_dict() for lease in self._leases if lease.resource == resource],
                }
            return {
                "enabled": self.enabled,
                "mode": self._mode,
                "leases": [lease.to_dict() for lease in self._leases],
                "resources": resources,
                "conflicts": list(self._last_conflicts),
                "history": list(self._history[-20:]),
            }

    def _drop_expired_locked(self) -> None:
        now = time.monotonic()
        self._leases = [lease for lease in self._leases if not lease.expired(now)]

    def _find_owner_lease_locked(self, resource: Resource, owner: str) -> Optional[ResourceLease]:
        for lease in self._leases:
            if lease.resource == resource and lease.owner == owner:
                return lease
        return None

    def _conflicting_leases_locked(self, resource: Resource, owner: str) -> List[ResourceLease]:
        info = resource_info(resource)
        conflicts = []
        same_resource = [lease for lease in self._leases if lease.resource == resource and lease.owner != owner]
        if info.shared:
            if len(same_resource) >= max(1, info.max_shared):
                conflicts.extend(same_resource)
        else:
            conflicts.extend(same_resource)

        if info.conflict_group:
            for lease in self._leases:
                if lease.owner == owner:
                    continue
                other = resource_info(lease.resource)
                if other.conflict_group == info.conflict_group and lease.resource != resource:
                    conflicts.append(lease)
        return conflicts
