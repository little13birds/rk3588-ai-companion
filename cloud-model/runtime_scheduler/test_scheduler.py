"""Runtime scheduler unit samples. Run: python3 -m runtime_scheduler.test_scheduler"""
import time

from runtime_scheduler.modes import NORMAL_POLICY, READING_POLICY, PRIORITY_NORMAL, PRIORITY_READING
from runtime_scheduler.resources import Resource
from runtime_scheduler.scheduler import ResourceScheduler


def test_exclusive_resource_conflict():
    scheduler = ResourceScheduler(enabled=True)
    first = scheduler.acquire_many(
        owner="a",
        mode="normal",
        resources=[Resource.USB_V4L2_CAMERA],
        priority=PRIORITY_NORMAL,
    )
    second = scheduler.acquire_many(
        owner="b",
        mode="normal",
        resources=[Resource.USB_V4L2_CAMERA],
        priority=PRIORITY_NORMAL,
    )
    assert first.ok
    assert not second.ok
    assert second.conflicts
    print("test_exclusive_resource_conflict PASS")


def test_npu_preemption():
    scheduler = ResourceScheduler(enabled=True)
    safety = scheduler.acquire_many(
        owner="safety",
        mode="normal",
        resources=[Resource.NPU_SAFETY],
        priority=PRIORITY_NORMAL,
    )
    reading = scheduler.acquire_many(
        owner="reading",
        mode="reading",
        resources=[Resource.NPU_BOOK],
        priority=PRIORITY_READING,
        preempt=True,
    )
    assert safety.ok
    assert reading.ok
    assert reading.preempted
    leases = scheduler.snapshot()["leases"]
    assert len(leases) == 1
    assert leases[0]["resource"] == Resource.NPU_BOOK.value
    print("test_npu_preemption PASS")


def test_npu_core_resources_can_run_in_parallel():
    scheduler = ResourceScheduler(enabled=True)
    core0 = scheduler.acquire_many(
        owner="pose",
        mode="normal",
        resources=[Resource.NPU_CORE_0],
        priority=PRIORITY_NORMAL,
    )
    core1 = scheduler.acquire_many(
        owner="hand_hazard",
        mode="normal",
        resources=[Resource.NPU_CORE_1],
        priority=PRIORITY_NORMAL,
    )
    core2 = scheduler.acquire_many(
        owner="book_or_face",
        mode="reading",
        resources=[Resource.NPU_CORE_2],
        priority=PRIORITY_READING,
    )
    assert core0.ok
    assert core1.ok
    assert core2.ok
    assert len(scheduler.snapshot()["leases"]) == 3
    print("test_npu_core_resources_can_run_in_parallel PASS")


def test_mode_policies_use_physical_npu_cores():
    assert Resource.NPU_CORE_0 in NORMAL_POLICY.resources
    assert Resource.NPU_CORE_1 in NORMAL_POLICY.resources
    assert Resource.NPU_CORE_2 in READING_POLICY.resources
    assert Resource.NPU_SAFETY not in NORMAL_POLICY.resources
    assert Resource.NPU_BOOK not in READING_POLICY.resources
    print("test_mode_policies_use_physical_npu_cores PASS")


def test_shared_resource_limit():
    scheduler = ResourceScheduler(enabled=True)
    for idx in range(4):
        result = scheduler.acquire_many(
            owner=f"rgb{idx}",
            mode="normal",
            resources=[Resource.ROS_RGB_CAMERA],
            priority=PRIORITY_NORMAL,
        )
        assert result.ok
    blocked = scheduler.acquire_many(
        owner="rgb5",
        mode="normal",
        resources=[Resource.ROS_RGB_CAMERA],
        priority=PRIORITY_NORMAL,
    )
    assert not blocked.ok
    print("test_shared_resource_limit PASS")


def test_ttl_expiration():
    scheduler = ResourceScheduler(enabled=True)
    result = scheduler.acquire_many(
        owner="short",
        mode="normal",
        resources=[Resource.SPEAKER_TTS],
        priority=PRIORITY_NORMAL,
        ttl_sec=0.05,
    )
    assert result.ok
    time.sleep(0.08)
    assert scheduler.snapshot()["leases"] == []
    print("test_ttl_expiration PASS")


if __name__ == "__main__":
    test_exclusive_resource_conflict()
    test_npu_preemption()
    test_npu_core_resources_can_run_in_parallel()
    test_mode_policies_use_physical_npu_cores()
    test_shared_resource_limit()
    test_ttl_expiration()
    print("ALL PASS")
