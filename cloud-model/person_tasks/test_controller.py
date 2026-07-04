import os
import time

from person_tasks.controller import PersonTaskController


class FakeAdapter:
    def __init__(self):
        self.calls = []
        self.stop_calls = 0

    def control(self, action, target):
        self.calls.append((action, target))
        return {"ok": True, "action": action, "target": target, "target_name": target}

    def observe_people(self):
        return {"ok": True, "visible_people": []}

    def stop_person_tasks(self):
        self.stop_calls += 1


def test_seek_arrived_status_emits_event():
    statuses = [
        {"state": "SEARCH_ROTATE", "reason": "searching"},
        {"state": "ARRIVED", "reason": "arrived", "target_distance_m": 0.78},
    ]
    events = []

    def status_getter():
        if statuses:
            return statuses.pop(0)
        return {"state": "ARRIVED", "reason": "arrived", "target_distance_m": 0.78}

    controller = PersonTaskController(
        adapter=FakeAdapter(),
        seek_status_getter=status_getter,
        seek_monitor_interval_sec=0.01,
        seek_monitor_start_delay_sec=0.0,
    )
    controller.set_event_handler(events.append)

    try:
        result = controller.control("seek", "tao")
        deadline = time.time() + 1.0
        while not events and time.time() < deadline:
            time.sleep(0.01)

        assert result["ok"] is True
        assert events == [
            {
                "event": "seek_arrived",
                "target": "tao",
                "target_name": "tao",
                "status": {"state": "ARRIVED", "reason": "arrived", "target_distance_m": 0.78},
            }
        ]
    finally:
        controller.shutdown()


def test_stop_cancels_seek_arrived_monitor():
    events = []
    controller = PersonTaskController(
        adapter=FakeAdapter(),
        seek_status_getter=lambda: {"state": "ARRIVED", "reason": "arrived"},
        seek_monitor_interval_sec=0.5,
        seek_monitor_start_delay_sec=0.2,
    )
    controller.set_event_handler(events.append)

    try:
        controller.control("seek", "tao")
        controller.control("stop", "nearest")
        time.sleep(0.05)

        assert events == []
    finally:
        controller.shutdown()


def test_initial_idle_status_does_not_cancel_seek_arrival_monitor():
    statuses = [
        {"state": "IDLE", "reason": "startup"},
        {"state": "SEARCH_ROTATE", "reason": "searching"},
        {"state": "ARRIVED", "reason": "arrived", "target_distance_m": 0.65},
    ]
    events = []

    def status_getter():
        if statuses:
            return statuses.pop(0)
        return {"state": "ARRIVED", "reason": "arrived", "target_distance_m": 0.65}

    controller = PersonTaskController(
        adapter=FakeAdapter(),
        seek_status_getter=status_getter,
        seek_monitor_interval_sec=0.01,
        seek_monitor_start_delay_sec=0.0,
    )
    controller.set_event_handler(events.append)

    try:
        controller.control("seek", "tao")
        deadline = time.time() + 1.0
        while not events and time.time() < deadline:
            time.sleep(0.01)

        assert events and events[0]["event"] == "seek_arrived"
    finally:
        controller.shutdown()


def test_arrived_status_stops_seek_task_after_emitting_event():
    adapter = FakeAdapter()
    events = []
    controller = PersonTaskController(
        adapter=adapter,
        seek_status_getter=lambda: {"state": "ARRIVED", "reason": "arrived"},
        seek_monitor_interval_sec=0.01,
        seek_monitor_start_delay_sec=0.0,
    )
    controller.set_event_handler(events.append)

    try:
        controller.control("seek", "tao")
        deadline = time.time() + 1.0
        while adapter.stop_calls == 0 and time.time() < deadline:
            time.sleep(0.01)

        assert events and events[0]["event"] == "seek_arrived"
        assert adapter.stop_calls == 1
    finally:
        controller.shutdown()


def test_default_seek_monitor_timeout_covers_slow_room_search():
    controller = PersonTaskController(adapter=FakeAdapter())
    try:
        assert controller._seek_monitor_timeout_sec >= 180.0
    finally:
        controller.shutdown()


def test_seek_monitor_timeout_can_be_configured_by_environment():
    old_value = os.environ.get("PERSON_SEEK_MONITOR_TIMEOUT_SEC")
    os.environ["PERSON_SEEK_MONITOR_TIMEOUT_SEC"] = "240"
    try:
        controller = PersonTaskController(adapter=FakeAdapter())
        try:
            assert controller._seek_monitor_timeout_sec == 240.0
        finally:
            controller.shutdown()
    finally:
        if old_value is None:
            os.environ.pop("PERSON_SEEK_MONITOR_TIMEOUT_SEC", None)
        else:
            os.environ["PERSON_SEEK_MONITOR_TIMEOUT_SEC"] = old_value


if __name__ == "__main__":
    test_seek_arrived_status_emits_event()
    test_stop_cancels_seek_arrived_monitor()
    test_initial_idle_status_does_not_cancel_seek_arrival_monitor()
    test_arrived_status_stops_seek_task_after_emitting_event()
    test_default_seek_monitor_timeout_covers_slow_room_search()
    test_seek_monitor_timeout_can_be_configured_by_environment()
    print("ALL PASS")
