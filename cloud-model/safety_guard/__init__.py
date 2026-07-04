"""Safety guard integration for cloud-model."""

from .config import SafetyGuardConfig

__all__ = ["SafetyGuardConfig", "SafetyGuardService"]


def __getattr__(name):
    if name == "SafetyGuardService":
        from .service import SafetyGuardService

        return SafetyGuardService
    raise AttributeError(name)
