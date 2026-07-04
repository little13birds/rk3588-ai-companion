"""Tests for manual runtime CLI debug commands.

Run from repo root:
    python3 -m test_debug_runtime
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout

import debug_runtime


class FakeCoordinator:
    def __init__(self):
        self.calls = []

    def start_reading(self):
        self.calls.append(("start_reading",))
        return True

    def stop_reading(self, return_home=False):
        self.calls.append(("stop_reading", bool(return_home)))
        return True

    def pause_reading_page(self):
        self.calls.append(("pause_reading_page",))
        return True

    def snapshot(self):
        return {"mode": "reading", "reading": {"active": True}}


class FakeApp:
    def __init__(self):
        self.coordinator = FakeCoordinator()
        self.mode = "normal"
        self.shutdown_called = False
        self.platform_calls = []

    def start_platform(self):
        self.platform_calls.append("start")
        return 0

    def stop_platform(self):
        self.platform_calls.append("stop")
        return 0

    def print_status(self):
        print("status-called")

    def print_arm_status(self):
        print("arm-status-called")

    def save_snapshot(self):
        print("snapshot-called")

    def print_help(self):
        for name in debug_runtime.command_descriptions():
            print(name)

    def shutdown(self):
        self.shutdown_called = True


def test_command_table_contains_reading_flow_commands():
    commands = debug_runtime.command_table()
    for name in ("status", "enter-reading", "page-done", "next-page", "exit-reading", "quit"):
        assert name in commands


def test_enter_reading_starts_tracking_and_sets_mode():
    app = FakeApp()
    debug_runtime.command_table()["enter-reading"](app, [])
    assert app.coordinator.calls == [("start_reading",)]
    assert app.mode == "reading"


def test_page_done_matches_main_reading_page_pause():
    app = FakeApp()
    app.mode = "reading"
    debug_runtime.command_table()["page-done"](app, [])
    assert app.coordinator.calls == [("pause_reading_page",)]
    assert app.mode == "reading"


def test_exit_reading_returns_arm_home_and_sets_normal():
    app = FakeApp()
    app.mode = "reading"
    debug_runtime.command_table()["exit-reading"](app, [])
    assert app.coordinator.calls == [("stop_reading", True)]
    assert app.mode == "normal"


def test_next_page_restarts_reading_tracking():
    app = FakeApp()
    app.mode = "reading"
    debug_runtime.command_table()["next-page"](app, [])
    assert app.coordinator.calls == [("start_reading",)]
    assert app.mode == "reading"


def test_quit_requests_shutdown():
    app = FakeApp()
    result = debug_runtime.command_table()["quit"](app, [])
    assert result is False
    assert app.shutdown_called is True


def test_help_prints_available_commands():
    app = FakeApp()
    out = io.StringIO()
    with redirect_stdout(out):
        debug_runtime.command_table()["help"](app, [])
    text = out.getvalue()
    assert "enter-reading" in text
    assert "page-done" in text
    assert "exit-reading" in text


if __name__ == "__main__":
    test_command_table_contains_reading_flow_commands()
    print("test_command_table_contains_reading_flow_commands PASS")
    test_enter_reading_starts_tracking_and_sets_mode()
    print("test_enter_reading_starts_tracking_and_sets_mode PASS")
    test_page_done_matches_main_reading_page_pause()
    print("test_page_done_matches_main_reading_page_pause PASS")
    test_exit_reading_returns_arm_home_and_sets_normal()
    print("test_exit_reading_returns_arm_home_and_sets_normal PASS")
    test_next_page_restarts_reading_tracking()
    print("test_next_page_restarts_reading_tracking PASS")
    test_quit_requests_shutdown()
    print("test_quit_requests_shutdown PASS")
    test_help_prints_available_commands()
    print("test_help_prints_available_commands PASS")
    print("ALL PASS")
