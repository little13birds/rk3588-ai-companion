"""Chassis control adapter tests.

Run from repo root with: python3 -m dashboard.test_chassis_control
"""
import os

from dashboard.chassis_control import ChassisControlAdapter, ChassisControlConfig


class ManualClock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_disabled_adapter_keeps_reserved_safe_behavior():
    published = []
    adapter = ChassisControlAdapter(
        ChassisControlConfig(enabled=False),
        publisher=lambda linear, angular: published.append((linear, angular)),
    )
    result = adapter.move("forward")
    assert result["ok"] is True
    assert result["reserved"] is True
    assert published == []
    print("test_disabled_adapter_keeps_reserved_safe_behavior PASS")


def test_enabled_adapter_publishes_to_raw_intent_shape():
    published = []
    adapter = ChassisControlAdapter(
        ChassisControlConfig(enabled=True, linear_mps=0.2, angular_radps=0.5),
        publisher=lambda linear, angular: published.append((linear, angular)),
    )
    result = adapter.move("left")
    assert result["ok"] is True
    assert result["status"] == "published"
    assert published == [(0.0, 0.5)]
    print("test_enabled_adapter_publishes_to_raw_intent_shape PASS")


def test_default_speeds_match_teleop_twist_keyboard_defaults():
    config = ChassisControlConfig()
    assert config.linear_mps == 0.5
    assert config.angular_radps == 1.0
    print("test_default_speeds_match_teleop_twist_keyboard_defaults PASS")


def test_env_defaults_match_teleop_twist_keyboard_defaults():
    old_linear = os.environ.pop("DASHBOARD_CHASSIS_LINEAR_MPS", None)
    old_angular = os.environ.pop("DASHBOARD_CHASSIS_ANGULAR_RADPS", None)
    try:
        config = ChassisControlConfig.from_env()
        assert config.linear_mps == 0.5
        assert config.angular_radps == 1.0
    finally:
        if old_linear is not None:
            os.environ["DASHBOARD_CHASSIS_LINEAR_MPS"] = old_linear
        if old_angular is not None:
            os.environ["DASHBOARD_CHASSIS_ANGULAR_RADPS"] = old_angular
    print("test_env_defaults_match_teleop_twist_keyboard_defaults PASS")


def test_ros_publisher_uses_teleop_queue_depth_and_dedicated_executor():
    import types
    import sys

    calls = []

    class FakeNode:
        def create_publisher(self, _msg_type, topic, depth):
            calls.append(("create_publisher", topic, depth))
            return types.SimpleNamespace(publish=lambda msg: calls.append(("publish", msg)))

        def destroy_node(self):
            calls.append(("destroy_node",))

    class FakeExecutor:
        def add_node(self, node):
            calls.append(("executor_add_node", node))

        def spin(self):
            calls.append(("executor_spin",))

        def shutdown(self):
            calls.append(("executor_shutdown",))

    fake_rclpy = types.SimpleNamespace(
        ok=lambda: False,
        init=lambda args=None: calls.append(("init", args)),
        create_node=lambda name: (calls.append(("create_node", name)) or FakeNode()),
    )
    fake_rclpy_executors = types.ModuleType("rclpy.executors")
    fake_rclpy_executors.SingleThreadedExecutor = FakeExecutor
    fake_geometry_msgs = types.ModuleType("geometry_msgs")
    fake_geometry_msgs_msg = types.ModuleType("geometry_msgs.msg")
    fake_geometry_msgs_msg.Twist = lambda: types.SimpleNamespace(
        linear=types.SimpleNamespace(x=0.0),
        angular=types.SimpleNamespace(z=0.0),
    )
    fake_geometry_msgs.msg = fake_geometry_msgs_msg

    old_rclpy = sys.modules.get("rclpy")
    old_rclpy_executors = sys.modules.get("rclpy.executors")
    old_geometry_msgs = sys.modules.get("geometry_msgs")
    old_geometry_msgs_msg = sys.modules.get("geometry_msgs.msg")
    sys.modules["rclpy"] = fake_rclpy
    sys.modules["rclpy.executors"] = fake_rclpy_executors
    sys.modules["geometry_msgs"] = fake_geometry_msgs
    sys.modules["geometry_msgs.msg"] = fake_geometry_msgs_msg
    try:
        from dashboard.chassis_control import RosCmdVelRawPublisher

        publisher = RosCmdVelRawPublisher("/cmd_vel_raw")
        assert ("create_publisher", "/cmd_vel_raw", 10) in calls
        assert any(call[0] == "executor_add_node" for call in calls)
        assert any(call[0] == "executor_spin" for call in calls)
        publisher.publish(0.5, 1.0)
        assert calls[-1][0] == "publish"
    finally:
        for key, value in (
            ("rclpy", old_rclpy),
            ("rclpy.executors", old_rclpy_executors),
            ("geometry_msgs", old_geometry_msgs),
            ("geometry_msgs.msg", old_geometry_msgs_msg),
        ):
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value
    print("test_ros_publisher_uses_teleop_queue_depth_and_dedicated_executor PASS")


def test_emergency_stop_always_publishes_zero_when_enabled():
    published = []
    adapter = ChassisControlAdapter(
        ChassisControlConfig(enabled=True),
        publisher=lambda linear, angular: published.append((linear, angular)),
    )
    adapter.move("forward")
    result = adapter.emergency_stop()
    assert result["ok"] is True
    assert result["direction"] == "stop"
    assert published[-1] == (0.0, 0.0)
    print("test_emergency_stop_always_publishes_zero_when_enabled PASS")


def test_rate_limit_does_not_drop_stop():
    clock = ManualClock()
    published = []
    adapter = ChassisControlAdapter(
        ChassisControlConfig(enabled=True, min_interval_sec=1.0),
        publisher=lambda linear, angular: published.append((linear, angular)),
        clock=clock,
    )
    adapter.move("forward")
    limited = adapter.move("left")
    stopped = adapter.move("stop")
    assert limited["skipped"] is True
    assert stopped["status"] == "published"
    assert published[-1] == (0.0, 0.0)
    print("test_rate_limit_does_not_drop_stop PASS")


def test_enabled_status_reports_connected_when_publisher_exists():
    adapter = ChassisControlAdapter(
        ChassisControlConfig(enabled=True),
        publisher=lambda linear, angular: None,
    )
    status = adapter.status()
    assert status["enabled"] is True
    assert status["reserved"] is False
    assert status["status"] == "connected"
    assert "connected" in status["detail"]
    print("test_enabled_status_reports_connected_when_publisher_exists PASS")


if __name__ == "__main__":
    test_disabled_adapter_keeps_reserved_safe_behavior()
    test_enabled_adapter_publishes_to_raw_intent_shape()
    test_default_speeds_match_teleop_twist_keyboard_defaults()
    test_env_defaults_match_teleop_twist_keyboard_defaults()
    test_ros_publisher_uses_teleop_queue_depth_and_dedicated_executor()
    test_emergency_stop_always_publishes_zero_when_enabled()
    test_rate_limit_does_not_drop_stop()
    test_enabled_status_reports_connected_when_publisher_exists()
    print("ALL PASS")
