"""Runtime coordinator sample tests. Run: python3 -m runtime_scheduler.test_coordinator"""
import io
from contextlib import redirect_stdout

from runtime_scheduler.adapters.arm import ArmAgentAdapter
from runtime_scheduler.coordinator import RuntimeCoordinator
from runtime_scheduler.scheduler import ResourceScheduler


class FakeSafety:
    def __init__(self):
        self.pauses = []
        self.resumes = []

    def pause(self, reason):
        self.pauses.append(reason)

    def resume(self, reason):
        self.resumes.append(reason)

    def status(self):
        return {"paused": bool(self.pauses) and not bool(self.resumes)}


class FakePlatformCamera:
    def __init__(self):
        self.stops = []
        self.starts = []
        self.releases = []
        self.restores = []

    def stop(self, reason=""):
        self.stops.append(reason)
        return True

    def start(self, reason=""):
        self.starts.append(reason)
        return True

    def release_for_reading(self, reason=""):
        self.releases.append(reason)
        return True

    def restore_after_reading(self, reason=""):
        self.restores.append(reason)
        return True

    def status(self):
        return {
            "stops": len(self.stops),
            "starts": len(self.starts),
            "releases": len(self.releases),
            "restores": len(self.restores),
        }


class FakeArm(ArmAgentAdapter):
    def __init__(self, healthy=True, prepare_ok=True):
        super().__init__(auto_start=False)
        self.healthy = healthy
        self.prepare_ok = prepare_ok
        self.prepared = 0
        self.started = 0
        self.stopped = 0
        self.service_stopped = 0
        self.stop_return_home = []

    def health(self, require_frame=None):
        return {"ok": self.healthy, "frame_ok": self.healthy, "status_ok": self.healthy}

    def ensure_running(self):
        return self.health()

    def prepare_reading(self):
        self.prepared += 1
        return self.healthy and self.prepare_ok

    def start_reading(self):
        self.started += 1
        return self.healthy

    def stop_reading(self, return_home=False):
        self.stopped += 1
        self.stop_return_home.append(bool(return_home))
        return True

    def stop_service(self, reason=""):
        self.service_stopped += 1
        return True


def test_reading_pauses_and_resumes_safety():
    safety = FakeSafety()
    arm = FakeArm(healthy=True)
    coordinator = RuntimeCoordinator(ResourceScheduler(True), safety_guard=safety, arm=arm, platform_camera=FakePlatformCamera())
    coordinator.bootstrap()
    assert coordinator.start_reading()
    assert safety.pauses == ["reading"]
    assert arm.prepared == 1
    assert arm.started == 1
    assert coordinator.stop_reading()
    assert safety.resumes == ["reading_stop"]
    assert arm.stopped == 1
    assert arm.stop_return_home == [False]
    print("test_reading_pauses_and_resumes_safety PASS")


def test_unhealthy_arm_does_not_crash():
    safety = FakeSafety()
    arm = FakeArm(healthy=False)
    coordinator = RuntimeCoordinator(ResourceScheduler(True), safety_guard=safety, arm=arm, platform_camera=FakePlatformCamera())
    coordinator.bootstrap()
    assert coordinator.start_reading() is False
    snapshot = coordinator.snapshot()
    assert snapshot["reading"]["last_health"]["ok"] is False
    assert snapshot["mode"] == "normal"
    assert safety.resumes == ["reading_start_failed"]
    print("test_unhealthy_arm_does_not_crash PASS")



def test_prepare_failure_does_not_start_tracking():
    safety = FakeSafety()
    arm = FakeArm(healthy=True, prepare_ok=False)
    coordinator = RuntimeCoordinator(ResourceScheduler(True), safety_guard=safety, arm=arm, platform_camera=FakePlatformCamera())
    coordinator.bootstrap()
    assert coordinator.start_reading() is False
    assert arm.prepared == 1
    assert arm.started == 0
    assert safety.pauses == ["reading"]
    assert safety.resumes == ["reading_prepare_failed"]
    assert coordinator.snapshot()["mode"] == "normal"
    print("test_prepare_failure_does_not_start_tracking PASS")


def test_reading_stops_and_restores_platform_camera():
    safety = FakeSafety()
    arm = FakeArm(healthy=True)
    platform = FakePlatformCamera()
    coordinator = RuntimeCoordinator(
        ResourceScheduler(True), safety_guard=safety, arm=arm, platform_camera=platform
    )
    coordinator.bootstrap()
    assert coordinator.start_reading() is True
    assert platform.releases == ["reading"]
    assert platform.stops == []
    assert platform.restores == []
    assert coordinator.stop_reading() is True
    assert platform.restores == ["reading_stop"]
    assert platform.starts == []
    print("test_reading_stops_and_restores_platform_camera PASS")


def test_page_pause_keeps_reading_resources_and_platform_camera_stopped():
    safety = FakeSafety()
    arm = FakeArm(healthy=True)
    platform = FakePlatformCamera()
    coordinator = RuntimeCoordinator(
        ResourceScheduler(True), safety_guard=safety, arm=arm, platform_camera=platform
    )
    coordinator.bootstrap()
    assert coordinator.start_reading() is True
    assert coordinator.pause_reading_page() is True
    assert arm.stopped == 1
    assert arm.service_stopped == 0
    assert arm.stop_return_home == [False]
    assert platform.restores == []
    assert safety.resumes == []
    snapshot = coordinator.snapshot()
    assert snapshot["mode"] == "reading", snapshot
    assert snapshot["reading"]["active"] is False, snapshot
    assert snapshot["reading"]["resources_held"] is True, snapshot
    print("test_page_pause_keeps_reading_resources_and_platform_camera_stopped PASS")


def test_next_page_reuses_reading_state_without_prepare():
    safety = FakeSafety()
    arm = FakeArm(healthy=True)
    platform = FakePlatformCamera()
    coordinator = RuntimeCoordinator(
        ResourceScheduler(True), safety_guard=safety, arm=arm, platform_camera=platform
    )
    coordinator.bootstrap()
    assert coordinator.start_reading() is True
    assert coordinator.pause_reading_page() is True
    assert coordinator.start_reading() is True
    assert platform.releases == ["reading"], platform.releases
    assert platform.restores == []
    assert safety.pauses == ["reading"], safety.pauses
    assert safety.resumes == []
    assert arm.prepared == 1
    assert arm.started == 2
    assert coordinator.snapshot()["reading"]["resources_held"] is True
    print("test_next_page_reuses_reading_state_without_prepare PASS")


def test_next_page_logs_prepare_skipped():
    safety = FakeSafety()
    arm = FakeArm(healthy=True)
    platform = FakePlatformCamera()
    coordinator = RuntimeCoordinator(
        ResourceScheduler(True), safety_guard=safety, arm=arm, platform_camera=platform
    )
    out = io.StringIO()
    with redirect_stdout(out):
        coordinator.bootstrap()
        assert coordinator.start_reading() is True
        assert coordinator.pause_reading_page() is True
        assert coordinator.start_reading() is True

    text = out.getvalue()
    assert "event=reading_resources_reused" in text
    assert "event=arm_prepare_skipped reason=next_page_reuse" in text
    assert text.count("event=arm_prepare_begin endpoint=/reading/prepare") == 1
    print("test_next_page_logs_prepare_skipped PASS")


def test_exit_reading_stops_arm_service_before_restoring_platform_camera():
    safety = FakeSafety()
    arm = FakeArm(healthy=True)
    platform = FakePlatformCamera()
    coordinator = RuntimeCoordinator(
        ResourceScheduler(True), safety_guard=safety, arm=arm, platform_camera=platform
    )
    coordinator.bootstrap()
    assert coordinator.start_reading() is True
    assert coordinator.stop_reading(return_home=True) is True
    assert arm.stop_return_home == [True]
    assert arm.service_stopped == 1
    assert platform.restores == ["reading_stop"]
    assert coordinator.snapshot()["reading"]["resources_held"] is False
    print("test_exit_reading_stops_arm_service_before_restoring_platform_camera PASS")


def test_arm_health_failure_restores_platform_camera():
    safety = FakeSafety()
    arm = FakeArm(healthy=False)
    platform = FakePlatformCamera()
    coordinator = RuntimeCoordinator(
        ResourceScheduler(True), safety_guard=safety, arm=arm, platform_camera=platform
    )
    coordinator.bootstrap()
    assert coordinator.start_reading() is False
    assert platform.releases == ["reading"]
    assert platform.restores == ["reading_start_failed"]
    assert safety.resumes == ["reading_start_failed"]
    print("test_arm_health_failure_restores_platform_camera PASS")

def test_disabled_scheduler_keeps_direct_arm_path():
    safety = FakeSafety()
    arm = FakeArm(healthy=True)
    coordinator = RuntimeCoordinator(ResourceScheduler(False), safety_guard=safety, arm=arm)
    assert coordinator.start_reading() is True
    assert coordinator.stop_reading() is True
    assert arm.prepared == 1
    assert arm.started == 1
    assert arm.stopped == 1
    assert safety.pauses == []
    assert safety.resumes == []
    print("test_disabled_scheduler_keeps_direct_arm_path PASS")


def test_stop_reading_can_return_arm_home():
    safety = FakeSafety()
    arm = FakeArm(healthy=True)
    coordinator = RuntimeCoordinator(ResourceScheduler(True), safety_guard=safety, arm=arm, platform_camera=FakePlatformCamera())
    coordinator.bootstrap()
    assert coordinator.start_reading() is True
    assert coordinator.stop_reading(return_home=True) is True
    assert arm.stop_return_home == [True]
    print("test_stop_reading_can_return_arm_home PASS")


def test_scheduler_logs_full_reading_transition():
    safety = FakeSafety()
    arm = FakeArm(healthy=True)
    platform = FakePlatformCamera()
    coordinator = RuntimeCoordinator(
        ResourceScheduler(True), safety_guard=safety, arm=arm, platform_camera=platform
    )
    out = io.StringIO()
    with redirect_stdout(out):
        coordinator.bootstrap()
        assert coordinator.start_reading() is True
        assert coordinator.stop_reading(return_home=True) is True
    text = out.getvalue()
    assert "event=bootstrap_normal" in text
    assert "event=reading_start_requested" in text
    assert "event=resources_acquired mode=reading" in text
    assert "event=safety_paused reason=reading" in text
    assert "event=platform_camera_release_ok reason=reading" in text
    assert "event=arm_health_ok mode=reading" in text
    assert "event=arm_prepare_ok endpoint=/reading/prepare" in text
    assert "event=arm_start_ok endpoint=/reading/start" in text
    assert "event=reading_started" in text
    assert "event=reading_stop_requested return_home=True" in text
    assert "event=arm_stop_ok return_home=True" in text
    assert "event=platform_camera_restore_ok reason=reading_stop" in text
    assert "event=normal_restored reason=reading_stop" in text
    print("test_scheduler_logs_full_reading_transition PASS")


if __name__ == "__main__":
    test_reading_pauses_and_resumes_safety()
    test_unhealthy_arm_does_not_crash()
    test_prepare_failure_does_not_start_tracking()
    test_reading_stops_and_restores_platform_camera()
    test_arm_health_failure_restores_platform_camera()
    test_page_pause_keeps_reading_resources_and_platform_camera_stopped()
    test_next_page_reuses_reading_state_without_prepare()
    test_next_page_logs_prepare_skipped()
    test_exit_reading_stops_arm_service_before_restoring_platform_camera()
    test_disabled_scheduler_keeps_direct_arm_path()
    test_stop_reading_can_return_arm_home()
    test_scheduler_logs_full_reading_transition()
    print("ALL PASS")
