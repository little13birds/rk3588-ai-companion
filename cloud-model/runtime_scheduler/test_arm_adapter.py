"""ArmAgentAdapter regression tests.

Run on board from ~/cloud-model-safety-mainline:
    python3 -m runtime_scheduler.test_arm_adapter
"""
from __future__ import annotations

from types import SimpleNamespace

from runtime_scheduler.adapters import arm as arm_adapter


def test_auto_start_waits_until_frame_health_is_ready() -> None:
    health_calls = []
    sleep_calls = []
    health_sequence = [
        {"ok": False, "status_ok": True, "frame_ok": False, "errors": ["frame_unavailable"]},
        {"ok": False, "status_ok": True, "frame_ok": False, "errors": ["frame_unavailable"]},
        {"ok": True, "status_ok": True, "frame_ok": True, "errors": []},
    ]

    def fake_health(require_frame=True, timeout=1.5):
        health_calls.append((require_frame, timeout))
        if health_sequence:
            return dict(health_sequence.pop(0))
        return {"ok": True, "status_ok": True, "frame_ok": True, "errors": []}

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="Started arm_agent\nReading arm started successfully.\n")

    old_health = arm_adapter.agent_client.health
    old_run = arm_adapter.subprocess.run
    old_time = getattr(arm_adapter, "time", None)
    old_sleep = getattr(old_time, "sleep", None) if old_time is not None else None
    try:
        arm_adapter.agent_client.health = fake_health
        arm_adapter.subprocess.run = fake_run
        if old_time is None:
            arm_adapter.time = SimpleNamespace(
                monotonic=lambda: 0.0,
                sleep=lambda seconds: sleep_calls.append(seconds),
            )
        else:
            arm_adapter.time.sleep = lambda seconds: sleep_calls.append(seconds)

        adapter = arm_adapter.ArmAgentAdapter(
            start_script="/tmp/start_reading_arm.sh",
            auto_start=True,
            require_frame=True,
            start_health_wait_sec=3.0,
            start_health_poll_sec=0.1,
        )
        health = adapter.ensure_running()
    finally:
        arm_adapter.agent_client.health = old_health
        arm_adapter.subprocess.run = old_run
        if old_time is None:
            delattr(arm_adapter, "time")
        else:
            arm_adapter.time.sleep = old_sleep

    assert health["ok"] is True, health
    assert health["auto_start"] is True, health
    assert health["start_returncode"] == 0, health
    assert health["health_attempts"] >= 2, health
    assert len(health_calls) >= 3, health_calls
    assert sleep_calls, sleep_calls


def test_stop_service_runs_configured_stop_script() -> None:
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="Reading arm stopped.\n")

    old_run = arm_adapter.subprocess.run
    try:
        arm_adapter.subprocess.run = fake_run
        adapter = arm_adapter.ArmAgentAdapter(stop_script="/tmp/stop_reading_arm.sh")
        assert adapter.stop_service("reading_stop") is True
    finally:
        arm_adapter.subprocess.run = old_run

    assert calls, calls
    assert calls[0][0][0] == ["/tmp/stop_reading_arm.sh"], calls
    assert calls[0][1]["timeout"] == 20.0, calls


def test_return_home_waits_before_service_shutdown() -> None:
    stop_calls = []
    sleep_calls = []

    def fake_stop_reading(return_home=False):
        stop_calls.append(bool(return_home))
        return True

    old_stop_reading = arm_adapter.agent_client.stop_reading
    old_sleep = arm_adapter.time.sleep
    try:
        arm_adapter.agent_client.stop_reading = fake_stop_reading
        arm_adapter.time.sleep = lambda seconds: sleep_calls.append(seconds)
        adapter = arm_adapter.ArmAgentAdapter(return_home_settle_sec=2.5)
        assert adapter.stop_reading(return_home=True) is True
        assert adapter.stop_reading(return_home=False) is True
    finally:
        arm_adapter.agent_client.stop_reading = old_stop_reading
        arm_adapter.time.sleep = old_sleep

    assert stop_calls == [True, False], stop_calls
    assert sleep_calls == [2.5], sleep_calls


if __name__ == "__main__":
    test_auto_start_waits_until_frame_health_is_ready()
    test_stop_service_runs_configured_stop_script()
    test_return_home_waits_before_service_shutdown()
    print("ALL PASS")
