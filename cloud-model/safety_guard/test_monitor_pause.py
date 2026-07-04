"""SafetyMonitor pause/resume samples. Run: python3 -m safety_guard.test_monitor_pause"""
import sys
import time
import types

from safety_guard.config import SafetyGuardConfig

sys.modules.setdefault("cv2", types.SimpleNamespace())
sys.modules.setdefault(
    "safety_guard.rknn_runtime",
    types.SimpleNamespace(SafetyRknnRuntime=object),
)
sys.modules.setdefault(
    "safety_guard.ros_camera",
    types.SimpleNamespace(RosRgbCamera=object),
)

from safety_guard.monitor import SafetyMonitor


class FakeCamera:
    def latest_bgr(self):
        return object(), time.monotonic()


class FakeRuntime:
    def __init__(self):
        self.calls = 0

    def process(self, frame, now, run_hazard):
        self.calls += 1
        return b"jpg", {}


def test_pause_skips_runtime_processing():
    runtime = FakeRuntime()
    monitor = SafetyMonitor(SafetyGuardConfig(enabled=True), runtime, FakeCamera(), lambda candidate: None)
    monitor.pause("unit_test")
    monitor._tick(time.monotonic())
    assert runtime.calls == 0
    status = monitor.status()
    assert status["paused"] is True
    assert status["pause_reason"] == "unit_test"
    monitor.resume("unit_test_done")
    monitor._tick(time.monotonic())
    assert runtime.calls == 1
    assert monitor.status()["paused"] is False
    print("test_pause_skips_runtime_processing PASS")


if __name__ == "__main__":
    test_pause_skips_runtime_processing()
    print("ALL PASS")
