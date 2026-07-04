"""Adapter around SafetyGuardService."""
from __future__ import annotations

from typing import Dict


class SafetyGuardAdapter:
    def __init__(self, service=None):
        self.service = service

    def pause(self, reason: str) -> bool:
        if not self.service or not hasattr(self.service, "pause"):
            return False
        self.service.pause(reason)
        return True

    def resume(self, reason: str) -> bool:
        if not self.service or not hasattr(self.service, "resume"):
            return False
        self.service.resume(reason)
        return True

    def status(self) -> Dict[str, object]:
        if self.service and hasattr(self.service, "status"):
            return self.service.status()
        return {"available": bool(self.service)}
