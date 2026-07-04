#!/usr/bin/env python3
"""Keyboard jog publisher for manually stepping each reading-arm joint.

The node publishes /joint_jog as Float32MultiArray:
    [delta_j1, delta_j2, delta_j3, delta_j4]

servo_controller owns the current joint command, applies limits, and publishes
/joint_states for roarm_driver.
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
    "q": (0, 1.0),
    "a": (0, -1.0),
    "w": (1, 1.0),
    "s": (1, -1.0),
    "e": (2, 1.0),
    "d": (2, -1.0),
    "r": (3, 1.0),
    "f": (3, -1.0),
}

AXIS_LABELS = ("j1", "j2", "j3", "j4")


@dataclass
class JointJogCommand:
    step: float = 0.02
    deltas: list = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    axis_index: int | None = None
    direction: float = 0.0

    def clear(self):
        self.deltas = [0.0, 0.0, 0.0, 0.0]
        self.axis_index = None
        self.direction = 0.0

    def set_axis_delta(self, axis_index: int, direction: float):
        self.clear()
        self.axis_index = axis_index
        self.direction = direction
        self.deltas[axis_index] = direction * self.step


def apply_key(command: JointJogCommand, key: str) -> bool:
    """Apply one keyboard command. Return False when the caller should quit."""
    command.clear()
    if key in ("\x03", "\x04"):  # Ctrl-C / Ctrl-D
        return False

    binding = KEY_BINDINGS.get(key)
    if binding is None:
        return True

    axis_index, direction = binding
    command.set_axis_delta(axis_index, direction)
    return True


HELP = """
Keyboard joint jog publisher

Keys:
  q / a    j1 + / -
  w / s    j2 + / -
  e / d    j3 + / -
  r / f    j4 + / -
  Ctrl-C   quit
"""


class KeyboardJointJog(Node):
    def __init__(self, step: float):
        super().__init__("keyboard_joint_jog")
        self.command = JointJogCommand(step=step)
        self.pub = self.create_publisher(Float32MultiArray, "/joint_jog", 10)

    def publish_command(self):
        self.pub.publish(Float32MultiArray(data=[float(x) for x in self.command.deltas]))

    def update_from_key(self, key: str) -> bool:
        keep_running = apply_key(self.command, key)
        if not keep_running:
            return False
        if self.command.axis_index is None:
            return True

        self.publish_command()
        axis = AXIS_LABELS[self.command.axis_index]
        sign = "+" if self.command.direction > 0 else "-"
        self.get_logger().info(
            "{} {}{:.3f} rad".format(axis, sign, abs(self.command.deltas[self.command.axis_index]))
        )
        return True


def _read_key(timeout: float = 0.05) -> str:
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if not ready:
        return ""
    return sys.stdin.read(1)


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Publish keyboard-controlled /joint_jog deltas for servo_controller.",
    )
    parser.add_argument(
        "--step",
        type=float,
        default=0.02,
        help="Per-key joint delta in radians. Default 0.02.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    if not sys.stdin.isatty():
        raise SystemExit("keyboard_joint_jog must run in an interactive terminal")

    rclpy.init(args=argv)
    node = KeyboardJointJog(step=max(0.001, args.step))

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
