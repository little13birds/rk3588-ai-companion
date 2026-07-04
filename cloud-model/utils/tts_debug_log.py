"""File-only JSONL diagnostics for LLM/TTS streaming issues."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


_LOG_LOCK = threading.Lock()
_DISABLED_VALUES = {"", "0", "false", "no", "off", "none", "disable", "disabled"}


def _default_log_path() -> Path:
    return Path(os.environ.get("TTS_DEBUG_LOG", "logs/tts_debug.jsonl")).expanduser()


def _is_disabled(path_value: Optional[str]) -> bool:
    if path_value is None:
        return False
    return path_value.strip().lower() in _DISABLED_VALUES


def text_tail(text: Any, limit: int = 80) -> Dict[str, Any]:
    value = str(text or "")
    limit = max(1, int(limit or 80))
    return {"len": len(value), "tail": value[-limit:]}


def log_tts_event(event: str, *, log_path: Optional[str] = None, **fields: Any) -> bool:
    """Append a JSONL diagnostic record. Never prints to console."""
    path_value = log_path if log_path is not None else os.environ.get("TTS_DEBUG_LOG")
    if _is_disabled(path_value):
        return False
    path = Path(path_value).expanduser() if path_value is not None else _default_log_path()
    record: Dict[str, Any] = {
        "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "pid": os.getpid(),
        "event": str(event),
    }
    record.update(fields)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(record, ensure_ascii=False, default=str)
        with _LOG_LOCK:
            with path.open("a", encoding="utf-8") as f:
                f.write(raw + "\n")
        return True
    except Exception:
        return False

