"""Small stdout coordination helpers for mixed streaming/log output."""

from __future__ import annotations

import builtins
import contextlib
import sys
import threading
from typing import IO, Iterator, Optional


_PRINT_LOCK = threading.RLock()
_STREAM_LOCK = threading.Lock()
_STREAM_DEPTH = 0


def is_console_streaming() -> bool:
    with _STREAM_LOCK:
        return _STREAM_DEPTH > 0


@contextlib.contextmanager
def console_stream() -> Iterator[None]:
    """Mark a live LLM text stream so low-priority logs can avoid interleaving."""
    global _STREAM_DEPTH
    with _STREAM_LOCK:
        _STREAM_DEPTH += 1
    try:
        yield
    finally:
        with _STREAM_LOCK:
            _STREAM_DEPTH = max(0, _STREAM_DEPTH - 1)


def console_write(text: object, *, file: Optional[IO[str]] = None, flush: bool = True) -> bool:
    stream = file or sys.stdout
    with _PRINT_LOCK:
        stream.write(str(text or ""))
        if flush:
            stream.flush()
    return True


def console_print(
    *args: object,
    sep: str = " ",
    end: str = "\n",
    file: Optional[IO[str]] = None,
    flush: bool = True,
    defer_during_stream: bool = False,
) -> bool:
    """Thread-safe print. Return False when a low-priority log is skipped."""
    if defer_during_stream and is_console_streaming():
        return False
    with _PRINT_LOCK:
        builtins.print(*args, sep=sep, end=end, file=file or sys.stdout, flush=flush)
    return True
