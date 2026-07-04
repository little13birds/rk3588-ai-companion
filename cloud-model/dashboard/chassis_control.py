"""Safe parent-dashboard chassis command adapter.

This module intentionally does not change ROS packages. It publishes parent
dashboard motion intent to `/cmd_vel_raw` when enabled, leaving the existing
obstacle guard responsible for producing the final `/cmd_vel`.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


VALID_DIRECTIONS = {"forward", "backward", "left", "right", "stop"}


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class ChassisControlConfig:
    enabled: bool = False
    cmd_vel_raw_topic: str = "/cmd_vel_raw"
    linear_mps: float = 0.5
    angular_radps: float = 1.0
    min_interval_sec: float = 0.08

    @classmethod
    def from_env(cls) -> "ChassisControlConfig":
        return cls(
            enabled=_bool_env("DASHBOARD_CHASSIS_CONTROL_ENABLED", False),
            cmd_vel_raw_topic=os.environ.get("DASHBOARD_CHASSIS_CMD_VEL_RAW_TOPIC", "/cmd_vel_raw"),
            linear_mps=max(0.0, _float_env("DASHBOARD_CHASSIS_LINEAR_MPS", 0.5)),
            angular_radps=max(0.0, _float_env("DASHBOARD_CHASSIS_ANGULAR_RADPS", 1.0)),
            min_interval_sec=max(0.02, _float_env("DASHBOARD_CHASSIS_MIN_INTERVAL_SEC", 0.08)),
        )


class ChassisControlAdapter:
    """Translate dashboard commands into safe chassis intent."""

    def __init__(
        self,
        config: Optional[ChassisControlConfig] = None,
        publisher: Optional[Callable[[float, float], None]] = None,
        clock: Optional[Callable[[], float]] = None,
    ):
        self.config = config or ChassisControlConfig.from_env()
        self._publisher = publisher
        self._clock = clock or time.monotonic
        self._last_publish_t = 0.0
        self._last_direction = "stop"
        self._last_error = ""

    @classmethod
    def from_env(cls) -> "ChassisControlAdapter":
        config = ChassisControlConfig.from_env()
        publisher = None
        adapter = cls(config)
        if config.enabled:
            try:
                ros_pub = RosCmdVelRawPublisher(config.cmd_vel_raw_topic)
                publisher = ros_pub.publish
                adapter._ros_publisher = ros_pub
            except Exception as exc:
                adapter._last_error = "ros_publisher_init_failed:{}".format(type(exc).__name__)
        if publisher:
            adapter._publisher = publisher
        return adapter

    def status(self) -> Dict[str, Any]:
        if not self.config.enabled:
            status = "reserved"
            detail = "dashboard chassis control is disabled"
        elif self._publisher:
            status = "connected"
            detail = "dashboard chassis adapter is connected"
        else:
            status = "error"
            detail = self._last_error or "publisher_unavailable"
        return {
            "enabled": bool(self.config.enabled),
            "reserved": not bool(self.config.enabled),
            "status": status,
            "detail": detail,
            "topic": self.config.cmd_vel_raw_topic,
            "last_direction": self._last_direction,
            "last_error": self._last_error,
        }

    def move(self, direction: str) -> Dict[str, Any]:
        direction = str(direction or "stop").strip().lower()
        if direction not in VALID_DIRECTIONS:
            return self._result(False, direction, "invalid_direction")
        if not self.config.enabled:
            self._last_direction = direction
            return self._result(True, direction, "reserved", reserved=True)
        if not self._publisher:
            self._last_error = "publisher_unavailable"
            return self._result(False, direction, self._last_error)

        now = self._clock()
        if direction != "stop" and now - self._last_publish_t < self.config.min_interval_sec:
            return self._result(True, direction, "rate_limited", skipped=True)

        linear_x, angular_z = self._twist(direction)
        self._publisher(linear_x, angular_z)
        self._last_publish_t = now
        self._last_direction = direction
        self._last_error = ""
        return self._result(True, direction, "published", linear_x=linear_x, angular_z=angular_z)

    def emergency_stop(self) -> Dict[str, Any]:
        if self.config.enabled and self._publisher:
            self._publisher(0.0, 0.0)
            self._last_publish_t = self._clock()
        self._last_direction = "stop"
        return self._result(True, "stop", "emergency_stop", linear_x=0.0, angular_z=0.0)

    def find_child(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "reserved": True,
            "action": "find_child",
            "reason": "person_seek_not_connected",
        }

    def handle_dashboard_command(self, command: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = payload or {}
        if command == "move":
            return self.move(str(payload.get("direction") or "stop"))
        if command == "emergency_stop":
            return self.emergency_stop()
        if command == "find_child":
            return self.find_child()
        return {"ok": False, "error": "unknown_command", "command": command}

    def _twist(self, direction: str) -> tuple[float, float]:
        if direction == "forward":
            return self.config.linear_mps, 0.0
        if direction == "backward":
            return -self.config.linear_mps, 0.0
        if direction == "left":
            return 0.0, self.config.angular_radps
        if direction == "right":
            return 0.0, -self.config.angular_radps
        return 0.0, 0.0

    @staticmethod
    def _result(
        ok: bool,
        direction: str,
        status: str,
        *,
        reserved: bool = False,
        skipped: bool = False,
        linear_x: Optional[float] = None,
        angular_z: Optional[float] = None,
    ) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "ok": ok,
            "direction": direction,
            "status": status,
            "reserved": reserved,
        }
        if skipped:
            data["skipped"] = True
        if linear_x is not None:
            data["linear_x"] = linear_x
        if angular_z is not None:
            data["angular_z"] = angular_z
        return data


class RosCmdVelRawPublisher:
    """Lazy ROS2 publisher for geometry_msgs/Twist on /cmd_vel_raw."""

    def __init__(self, topic: str):
        import rclpy
        from geometry_msgs.msg import Twist
        from rclpy.executors import SingleThreadedExecutor

        self._rclpy = rclpy
        self._twist_cls = Twist
        if not rclpy.ok():
            rclpy.init(args=None)
        self._node = rclpy.create_node("dashboard_chassis_control")
        self._publisher = self._node.create_publisher(Twist, topic, 10)
        self._executor = SingleThreadedExecutor()
        self._executor.add_node(self._node)
        self._spin_thread = threading.Thread(
            target=self._executor.spin,
            name="dashboard-chassis-ros-spin",
            daemon=True,
        )
        self._spin_thread.start()

    def publish(self, linear_x: float, angular_z: float) -> None:
        msg = self._twist_cls()
        msg.linear.x = float(linear_x)
        msg.angular.z = float(angular_z)
        self._publisher.publish(msg)

    def close(self) -> None:
        try:
            self._executor.shutdown()
        except Exception:
            pass
        try:
            self._node.destroy_node()
        except Exception:
            pass
