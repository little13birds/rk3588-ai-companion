"""Optional HDMI eye display controller.

This module is intentionally a thin adapter around ``eye_engine``.  The main
assistant can call it freely; when the HDMI/Pygame stack is disabled or fails,
all methods degrade to no-ops so voice, safety, reading, and person tasks keep
running.
"""
from __future__ import annotations

import os
from typing import Optional


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


class EyeDisplayController:
    """Safe facade for the HDMI eye GUI."""

    MODE_TO_EXPRESSION = {
        "normal": "neutral",
        "listen": "neutral",
        "awake": "neutral",
        "thinking": "thinking",
        "processing": "thinking",
        "speaking": "happy",
        "tts": "happy",
        "reading": "reading",
        "story": "happy",
        "following": "navigation",
        "seek": "navigation",
        "navigation": "navigation",
        "sleep": "sleepy",
        "sleepy": "sleepy",
    }

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.started = False
        self._state = None
        self._thread = None
        self._last_expression: Optional[str] = None

    @classmethod
    def from_env(cls) -> "EyeDisplayController":
        return cls(enabled=_bool_env("EYE_GUI_ENABLED", True))

    @property
    def expression(self) -> Optional[str]:
        if self._state is None:
            return self._last_expression
        try:
            expr, _trigger_time = self._state.snapshot()
            return expr
        except Exception:
            return self._last_expression

    def start(self) -> bool:
        if not self.enabled:
            print("[eye] event=disabled", flush=True)
            return False
        if self.started:
            return True
        try:
            from eye_engine.eye_state import EyeState
            import eye_engine

            self._state = EyeState()
            self._thread = eye_engine.start(self._state)
            self.started = True
            self.set_mode("sleep")
            print("[eye] event=started", flush=True)
            return True
        except Exception as exc:
            self._state = None
            self._thread = None
            self.started = False
            print("[eye] event=start_failed error_type={} error={}".format(
                type(exc).__name__, exc), flush=True)
            return False

    def set_mode(self, mode: str) -> None:
        expression = self.MODE_TO_EXPRESSION.get(str(mode or "").strip().lower())
        if not expression:
            return
        self._last_expression = expression
        if not self.started or self._state is None:
            return
        try:
            self._state.set_expression(expression)
        except Exception as exc:
            print("[eye] event=set_mode_failed mode={} error_type={} error={}".format(
                mode, type(exc).__name__, exc), flush=True)

    def blink(self) -> None:
        if not self.started or self._state is None:
            return
        try:
            self._state.trigger_blink()
        except Exception as exc:
            print("[eye] event=blink_failed error_type={} error={}".format(
                type(exc).__name__, exc), flush=True)
