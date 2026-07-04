"""Lease objects for scheduler-owned resources."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .resources import Resource


@dataclass
class ResourceLease:
    resource: Resource
    owner: str
    mode: str
    priority: int
    reason: str = ""
    preemptible: bool = True
    ttl_sec: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.monotonic)
    heartbeat_at: float = field(default_factory=time.monotonic)

    @property
    def expires_at(self) -> Optional[float]:
        if self.ttl_sec is None:
            return None
        return self.heartbeat_at + max(0.0, float(self.ttl_sec))

    def expired(self, now: Optional[float] = None) -> bool:
        expires_at = self.expires_at
        if expires_at is None:
            return False
        return (time.monotonic() if now is None else now) >= expires_at

    def refresh(self) -> None:
        self.heartbeat_at = time.monotonic()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource": self.resource.value,
            "owner": self.owner,
            "mode": self.mode,
            "priority": self.priority,
            "reason": self.reason,
            "preemptible": self.preemptible,
            "ttl_sec": self.ttl_sec,
            "age_sec": round(time.monotonic() - self.created_at, 3),
            "metadata": dict(self.metadata),
        }
