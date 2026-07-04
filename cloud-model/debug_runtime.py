"""Manual runtime debug CLI for camera and reading-mode scheduling.

This entry point intentionally skips ASR, LLM/VLM, microphone, and TTS. It uses
the same scheduler/safety/dashboard components as main.py so mode transitions
can be reproduced without speech or cloud model latency.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

import cv2

from dashboard import DashboardState, start_dashboard_server
from person_tasks import PersonTaskController, execute_person_tool
from runtime_scheduler import RuntimeCoordinator
from safety_guard import SafetyGuardConfig, SafetyGuardService


CommandFunc = Callable[["DebugRuntimeApp", List[str]], Optional[bool]]


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


class DebugRuntimeApp:
    def __init__(self, args):
        self.args = args
        self.mode = "normal"
        self.safety_guard = None
        self.dashboard_server = None
        self.dashboard_state = None
        self.coordinator = None
        self.person_task_controller = None
        self.platform_start_script = os.environ.get(
            "PLATFORM_CAMERA_START_SCRIPT",
            "scripts/start_platform_camera.sh",
        )
        self.platform_stop_script = os.environ.get(
            "PLATFORM_CAMERA_STOP_SCRIPT",
            "scripts/stop_platform_camera.sh",
        )

    def start(self) -> None:
        if not self.args.no_safety:
            config = SafetyGuardConfig.from_env()
            self.safety_guard = SafetyGuardService(config=config)
            self.safety_guard.start()
        else:
            print("[debug] event=safety_skipped", flush=True)

        self.dashboard_state = DashboardState.from_env()
        if self.safety_guard is not None:
            self.dashboard_state.set_camera_snapshot_provider(self.safety_guard.camera_snapshot)

        self.coordinator = RuntimeCoordinator.from_env(safety_guard=self.safety_guard)
        self.coordinator.bootstrap()
        self.dashboard_state.set_scheduler_status_provider(self.coordinator.snapshot)
        self.person_task_controller = PersonTaskController()
        self.stop_stale_person_tasks("startup_cleanup")

        if self.args.with_dashboard:
            self.dashboard_server = start_dashboard_server(self.dashboard_state)
            print("[debug] event=dashboard_started url=http://0.0.0.0:%s" % os.environ.get("DASHBOARD_PORT", "8080"), flush=True)

        print("[debug] event=ready mode=normal", flush=True)
        self.print_help()

    def shutdown(self) -> None:
        print("[debug] event=shutdown_begin", flush=True)
        self.stop_stale_person_tasks("shutdown_cleanup")
        try:
            if self.coordinator is not None and self.mode == "reading":
                self.coordinator.stop_reading(return_home=True)
        except Exception as exc:
            print(f"[debug] event=shutdown_reading_stop_failed error_type={type(exc).__name__} error={exc}", flush=True)
        if self.dashboard_server is not None:
            self.dashboard_server.stop()
            self.dashboard_server = None
        if self.safety_guard is not None:
            self.safety_guard.stop()
            self.safety_guard = None
        print("[debug] event=shutdown_done", flush=True)

    def start_platform(self) -> int:
        return self._run_script(self.platform_start_script, "platform_start")

    def stop_platform(self) -> int:
        return self._run_script(self.platform_stop_script, "platform_stop")

    def _run_script(self, script_path: str, event: str) -> int:
        script = os.path.expanduser(script_path)
        print(f"[debug] event={event}_begin script={script}", flush=True)
        try:
            result = subprocess.run(
                [script],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=self.args.script_timeout_sec,
            )
        except Exception as exc:
            print(f"[debug] event={event}_failed error_type={type(exc).__name__} error={exc}", flush=True)
            return 1
        if result.stdout:
            print(result.stdout.rstrip(), flush=True)
        print(f"[debug] event={event}_done returncode={result.returncode}", flush=True)
        return int(result.returncode)

    def print_status(self) -> None:
        status = {
            "mode": self.mode,
            "scheduler": self.coordinator.snapshot() if self.coordinator else {},
            "safety": self.safety_guard.status() if self.safety_guard else {"enabled": False},
        }
        print(_json(status), flush=True)

    def print_arm_status(self) -> None:
        from arm import agent_client

        print(_json(agent_client.health(require_frame=True, timeout=2.0)), flush=True)

    def save_snapshot(self) -> None:
        if not self.safety_guard:
            print("[debug] event=snapshot_failed reason=safety_unavailable", flush=True)
            return
        frame, _stamp = self.safety_guard._camera.latest_bgr() if self.safety_guard._camera else (None, 0.0)
        if frame is None:
            print("[debug] event=snapshot_failed reason=no_platform_rgb_frame", flush=True)
            return
        path = Path(self.args.snapshot_path).expanduser()
        ok = cv2.imwrite(str(path), frame)
        print(f"[debug] event=snapshot_saved ok={ok} path={path}", flush=True)

    def print_help(self) -> None:
        print("Commands:", flush=True)
        for name, description in command_descriptions().items():
            print(f"  {name:<14} {description}", flush=True)

    def run_person_tool(self, action: str, target: str = "nearest") -> None:
        result = execute_person_tool(
            "control_person_follow",
            json.dumps({"action": action, "target": target}, ensure_ascii=False),
            controller=self.person_task_controller,
        )
        print(_json(result), flush=True)

    def observe_people(self) -> None:
        result = execute_person_tool(
            "observe_people_identity",
            "{}",
            controller=self.person_task_controller,
        )
        print(_json(result), flush=True)

    def stop_stale_person_tasks(self, reason: str) -> None:
        if self.person_task_controller is None:
            return
        try:
            result = self.person_task_controller.control("stop", "nearest")
            print(f"[debug] event=person_task_stop reason={reason} result={_json(result)}", flush=True)
        except Exception as exc:
            print(
                f"[debug] event=person_task_stop_failed reason={reason} "
                f"error_type={type(exc).__name__} error={exc}",
                flush=True,
            )


def command_descriptions() -> Dict[str, str]:
    return {
        "help": "show commands",
        "status": "print scheduler, safety, and camera status",
        "start-platform": "run scripts/start_platform_camera.sh",
        "stop-platform": "run scripts/stop_platform_camera.sh",
        "enter-reading": "match main.py reading entry: coordinator.start_reading(); mode=reading",
        "page-done": "match main.py one-page done path: pause_reading_page(); keep mode=reading",
        "next-page": "match main.py continuing reading: coordinator.start_reading(); mode stays reading",
        "exit-reading": "match reading exit/interrupt/idle: stop_reading(return_home=True); mode=normal",
        "arm-status": "print arm_agent health and frame status",
        "snapshot": "save current platform RGB frame to snapshot path",
        "follow-me": "start person_follow nearest target",
        "follow-a": "start person_follow identity target role A/tao",
        "follow-b": "start person_follow identity target role B/xiao",
        "seek-a": "start person_seek identity target role A/tao",
        "seek-b": "start person_seek identity target role B/xiao",
        "stop-person": "stop active person_seek/person_follow nodes",
        "observe-people": "capture one frame and query person identity service",
        "quit": "cleanup and exit",
    }


def command_table() -> Dict[str, CommandFunc]:
    return {
        "help": _cmd_help,
        "status": _cmd_status,
        "start-platform": _cmd_start_platform,
        "stop-platform": _cmd_stop_platform,
        "enter-reading": _cmd_enter_reading,
        "page-done": _cmd_page_done,
        "next-page": _cmd_next_page,
        "exit-reading": _cmd_exit_reading,
        "arm-status": _cmd_arm_status,
        "snapshot": _cmd_snapshot,
        "follow-me": _cmd_follow_me,
        "follow-a": _cmd_follow_a,
        "follow-b": _cmd_follow_b,
        "seek-a": _cmd_seek_a,
        "seek-b": _cmd_seek_b,
        "stop-person": _cmd_stop_person,
        "observe-people": _cmd_observe_people,
        "quit": _cmd_quit,
        "exit": _cmd_quit,
    }


def _cmd_help(app: DebugRuntimeApp, _args: List[str]) -> Optional[bool]:
    app.print_help()
    return True


def _cmd_status(app: DebugRuntimeApp, _args: List[str]) -> Optional[bool]:
    app.print_status()
    return True


def _cmd_start_platform(app: DebugRuntimeApp, _args: List[str]) -> Optional[bool]:
    app.start_platform()
    return True


def _cmd_stop_platform(app: DebugRuntimeApp, _args: List[str]) -> Optional[bool]:
    app.stop_platform()
    return True


def _cmd_enter_reading(app: DebugRuntimeApp, _args: List[str]) -> Optional[bool]:
    ok = app.coordinator.start_reading()
    if ok:
        app.mode = "reading"
    print(f"[debug] event=enter_reading_done ok={ok} mode={app.mode}", flush=True)
    return True


def _cmd_page_done(app: DebugRuntimeApp, _args: List[str]) -> Optional[bool]:
    ok = app.coordinator.pause_reading_page()
    app.mode = "reading"
    print(f"[debug] event=page_done ok={ok} mode={app.mode} page_pause=True", flush=True)
    return True


def _cmd_next_page(app: DebugRuntimeApp, _args: List[str]) -> Optional[bool]:
    ok = app.coordinator.start_reading()
    if ok:
        app.mode = "reading"
    print(f"[debug] event=next_page_done ok={ok} mode={app.mode}", flush=True)
    return True


def _cmd_exit_reading(app: DebugRuntimeApp, _args: List[str]) -> Optional[bool]:
    ok = app.coordinator.stop_reading(return_home=True)
    if ok:
        app.mode = "normal"
    print(f"[debug] event=exit_reading_done ok={ok} mode={app.mode} return_home=True", flush=True)
    return True


def _cmd_arm_status(app: DebugRuntimeApp, _args: List[str]) -> Optional[bool]:
    app.print_arm_status()
    return True


def _cmd_snapshot(app: DebugRuntimeApp, _args: List[str]) -> Optional[bool]:
    app.save_snapshot()
    return True


def _cmd_follow_me(app: DebugRuntimeApp, _args: List[str]) -> Optional[bool]:
    app.run_person_tool("follow", "nearest")
    return True


def _cmd_follow_a(app: DebugRuntimeApp, _args: List[str]) -> Optional[bool]:
    app.run_person_tool("follow", "A")
    return True


def _cmd_follow_b(app: DebugRuntimeApp, _args: List[str]) -> Optional[bool]:
    app.run_person_tool("follow", "B")
    return True


def _cmd_seek_a(app: DebugRuntimeApp, _args: List[str]) -> Optional[bool]:
    app.run_person_tool("seek", "A")
    return True


def _cmd_seek_b(app: DebugRuntimeApp, _args: List[str]) -> Optional[bool]:
    app.run_person_tool("seek", "B")
    return True


def _cmd_stop_person(app: DebugRuntimeApp, _args: List[str]) -> Optional[bool]:
    app.run_person_tool("stop", "nearest")
    return True


def _cmd_observe_people(app: DebugRuntimeApp, _args: List[str]) -> Optional[bool]:
    app.observe_people()
    return True


def _cmd_quit(app: DebugRuntimeApp, _args: List[str]) -> Optional[bool]:
    app.shutdown()
    return False


def run_cli(app: DebugRuntimeApp) -> int:
    commands = command_table()
    while True:
        try:
            raw = input("debug> ")
        except (EOFError, KeyboardInterrupt):
            raw = "quit"
            print("", flush=True)
        parts = raw.strip().split()
        if not parts:
            continue
        name, args = parts[0], parts[1:]
        command = commands.get(name)
        if not command:
            print(f"[debug] event=unknown_command command={name}; type 'help'", flush=True)
            continue
        keep_running = command(app, args)
        if keep_running is False:
            return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Manual CLI debug for runtime scheduler and reading mode.")
    parser.add_argument("--no-safety", action="store_true", help="Do not start SafetyGuardService.")
    parser.add_argument("--with-dashboard", action="store_true", help="Start dashboard HTTP server too.")
    parser.add_argument("--snapshot-path", default="/tmp/debug_runtime_snapshot.jpg")
    parser.add_argument("--script-timeout-sec", type=float, default=35.0)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    app = DebugRuntimeApp(args)
    try:
        app.start()
        return run_cli(app)
    finally:
        if app.safety_guard is not None or app.dashboard_server is not None:
            app.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
