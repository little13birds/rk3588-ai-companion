#!/usr/bin/env python3
"""Keyboard publisher for semantic left/front/up direction testing.

The node publishes semantic /direction_jog as Float32MultiArray:
    [left_right, front_back, up_down]

Sign convention:
    right/front/down are +1, left/back/up are -1.
servo_controller maps these semantic signs to the observed hardware directions.
"""

import argparse
import select
import sys
import termios
import time
import tty
from dataclasses import dataclass, field

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32MultiArray


KEY_BINDINGS = {
    "a": (0, -1.0),  # left
    "d": (0, 1.0),   # right
    "w": (1, 1.0),   # front / near
    "s": (1, -1.0),  # back / far
    "q": (2, -1.0),  # up
    "e": (2, 1.0),   # down
}

DIRECTION_LABELS = (
    ("left", "right"),
    ("back", "front"),
    ("up", "down"),
)


@dataclass
class DirectionJogCommand:
    direction: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    axis_index: int | None = None

    def clear(self):
        self.direction = [0.0, 0.0, 0.0]
        self.axis_index = None

    def set_axis_direction(self, axis_index: int, sign: float):
        self.clear()
        self.axis_index = axis_index
        self.direction[axis_index] = sign


def apply_key(command: DirectionJogCommand, key: str) -> bool:
    """Apply one keyboard command. Return False when the caller should quit."""
    command.clear()
    if key in ("\x03", "\x04"):  # Ctrl-C / Ctrl-D
        return False

    binding = KEY_BINDINGS.get(key)
    if binding is None:
        return True

    axis_index, sign = binding
    command.set_axis_direction(axis_index, sign)
    return True


HELP = """
Keyboard direction jog publisher

Publishes semantic /direction_jog = [left_right, front_back, up_down]
Sign convention: right/front/down are +1, left/back/up are -1.

Keys:
  a / d    left / right
  w / s    front / back
  q / e    up / down
  Ctrl-C   quit
"""


class KeyboardDirectionJog(Node):
    def __init__(self):
        super().__init__("keyboard_direction_jog")
        self.command = DirectionJogCommand()
        self.pub = self.create_publisher(Float32MultiArray, "/direction_jog", 10)

    def publish_command(self):
        self.pub.publish(Float32MultiArray(data=[float(x) for x in self.command.direction]))

    def update_from_key(self, key: str) -> bool:
        keep_running = apply_key(self.command, key)
        if not keep_running:
            return False
        if self.command.axis_index is None:
            return True

        self.publish_command()
        direction_value = self.command.direction[self.command.axis_index]
        label_pair = DIRECTION_LABELS[self.command.axis_index]
        label = label_pair[1] if direction_value > 0 else label_pair[0]
        self.get_logger().info(
            "{} direction {}".format(label, self.command.direction)
        )
        return True


def _read_key(timeout: float = 0.05) -> str:
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if not ready:
        return ""
    return sys.stdin.read(1)


def _parse_args(argv=None):
    return argparse.ArgumentParser(
        description="Publish keyboard-controlled /direction_jog signs for servo testing.",
    ).parse_args(argv)


def main(argv=None):
    _parse_args(argv)
    if not sys.stdin.isatty():
        raise SystemExit("keyboard_direction_jog must run in an interactive terminal")

    rclpy.init(args=argv)
    node = KeyboardDirectionJog()

    old_settings = termios.tcgetattr(sys.stdin)
    print(HELP)
    try:
        tty.setcbreak(sys.stdin.fileno())
        while rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0.02)
            key = _read_key(timeout=0.02)
            if key and not node.update_from_key(key):
                break
            time.sleep(0.01)
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
