"""Tests for thread-safe console output coordination."""

import io
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.console_io import console_print, console_stream, console_write, is_console_streaming


def test_deferred_status_log_is_skipped_while_streaming():
    buf = io.StringIO()

    with console_stream():
        wrote = console_print("[处理中] loop=1", file=buf, defer_during_stream=True)
        console_write("[E", file=buf)
        console_write("ldricSage]", file=buf)

    assert wrote is False
    assert buf.getvalue() == "[EldricSage]"


def test_stream_flag_is_restored_after_context():
    assert is_console_streaming() is False

    with console_stream():
        assert is_console_streaming() is True

    assert is_console_streaming() is False


def test_deferred_status_log_does_not_interleave_threaded_stream():
    buf = io.StringIO()

    def stream_writer():
        with console_stream():
            console_write("[E", file=buf)
            time.sleep(0.05)
            console_write("ldricSage]", file=buf)

    thread = threading.Thread(target=stream_writer)
    thread.start()
    time.sleep(0.01)
    wrote = console_print("[处理中] loop=1", file=buf, defer_during_stream=True)
    thread.join()

    assert wrote is False
    assert buf.getvalue() == "[EldricSage]"


if __name__ == "__main__":
    test_deferred_status_log_is_skipped_while_streaming()
    test_stream_flag_is_restored_after_context()
    test_deferred_status_log_does_not_interleave_threaded_stream()
    print("ALL PASS")
