#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Yahboom X3 5-DOF Arm Controller — 3-Stage Visual Servoing

Stage 1 (CENTER):  dx_px→J1, dy_px→J2/3/4 vertical
Stage 2 (PITCH):   pitch_ratio→J2/3/4 tilt (decouples Y/Z)
Stage 3 (DISTANCE): avg_edge_px→J2/3/4 depth

Subscribes: /book_pose (yahboomcar_msgs/BookPose)
"""

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from yahboomcar_msgs.msg import BookPose
from Rosmaster_Lib import Rosmaster


class ArmController(Node):
    def __init__(self):
        super().__init__("arm_controller")

        # --- params ---
        self.declare_parameter("alpha", 0.2)              # stronger smoothing
        self.declare_parameter("input_scale", 0.3)        # global input scale (1.0 = original)

        self.declare_parameter("k_joint1", -30.0)
        self.declare_parameter("k_vertical", 30.0)
        self.declare_parameter("k_depth", 30.0)
        self.declare_parameter("k_pitch", 15.0)         # pitch_ratio → joint delta
        self.declare_parameter("kd_dx", -15.0)          # D term for dx (reduced, less jitter)
        self.declare_parameter("kd_dy", 15.0)           # D term for dy
        self.declare_parameter("kd_pitch", 8.0)         # D term for pitch
        self.declare_parameter("kd_edge", 15.0)         # D term for distance
        self.declare_parameter("run_time", 40)
        self.declare_parameter("control_rate", 20.0)

        # thresholds (from readme)
        self.declare_parameter("deadband_dx", 30)       # px, stage1→stage2
        self.declare_parameter("deadband_dy", 30)       # px
        self.declare_parameter("deadband_pitch", 0.02)  # ratio, stage2→stage3
        self.declare_parameter("deadband_edge", 50)     # px, stage3→idle
        self.declare_parameter("target_edge", 250.0)    # target avg_edge_px

        # safety
        self.declare_parameter("min_move", 2)            # min pulse per step (below = skip)
        self.declare_parameter("max_delta", 25)          # max pulse change per joint per step
        self.declare_parameter("max_dx", 640.0)          # reject |dx_px| above this
        self.declare_parameter("max_dy", 480.0)          # reject |dy_px| above this
        self.declare_parameter("max_pitch_err", 0.5)     # reject |pitch-1| above this
        self.declare_parameter("min_edge", 50.0)         # reject avg_edge below this
        self.declare_parameter("max_edge", 800.0)        # reject avg_edge above this
        self.declare_parameter("msg_timeout", 0.5)       # stop if no msg for N seconds

        # limits
        self.declare_parameter("j1_min", 1200)
        self.declare_parameter("j1_max", 2800)
        self.declare_parameter("j2_min", 1000)
        self.declare_parameter("j2_max", 3000)
        self.declare_parameter("j3_min", 1000)
        self.declare_parameter("j3_max", 3000)
        self.declare_parameter("j4_min", 1000)
        self.declare_parameter("j4_max", 3000)
        self.declare_parameter("j5_fixed", 1600)

        self._load_params()

        # state
        self.filtered = {"dx": 0.0, "dy": 0.0, "pitch": 1.0, "edge": 0.0}
        self._prev_err = {"dx": None, "dy": None, "pitch": None, "edge": None}
        self._accum = [0.0, 0.0, 0.0, 0.0, 0.0]  # fractional pulse accumulator
        self.mode = "INIT"
        self._msg_received = False
        self._last_msg_time = self.get_clock().now()

        # connect
        self.get_logger().info("Connecting STM32 /dev/myserial ...")
        self.car = Rosmaster(com="/dev/myserial", delay=0.005)
        self.car.create_receive_threading()
        self.car.set_uart_servo_torque(1)

        # read initial positions, clamp to limits
        self.pulses = [1500, 2000, 2000, 2000, self.j5_fixed]
        clipped = False
        limits = {
            1: (self.j1_min, self.j1_max), 2: (self.j2_min, self.j2_max),
            3: (self.j3_min, self.j3_max), 4: (self.j4_min, self.j4_max),
            5: (self.j5_fixed, self.j5_fixed),
        }
        for i in range(1, 6):
            val = self.car.get_uart_servo_value(i)
            if val and val[0] == i and val[1] > 0:
                raw = val[1]
                lo, hi = limits[i]
                if raw < lo or raw > hi:
                    self.get_logger().warn("Joint%d pulse %d out of [%d,%d], clamping" % (i, raw, lo, hi))
                    raw = max(lo, min(hi, raw))
                    clipped = True
                self.pulses[i - 1] = raw
        if clipped:
            self.get_logger().info("Moving to within limits...")
            for i in range(4):
                self.car.set_uart_servo(i + 1, self.pulses[i], run_time=500)
            import time; time.sleep(1.0)

        self.get_logger().info("Initial pulses: %s" % [int(p) for p in self.pulses])
        self.get_logger().info(
            "Limits: J1=[%d,%d] J2=[%d,%d] J3=[%d,%d] J4=[%d,%d] J5=%d"
            % (self.j1_min, self.j1_max, self.j2_min, self.j2_max,
               self.j3_min, self.j3_max, self.j4_min, self.j4_max, self.j5_fixed)
        )

        # subscriber
        self.sub = self.create_subscription(BookPose, "/book_pose", self._cb, 10)
        # publisher
        self.state_pub = self.create_publisher(String, "/arm_state", 10)
        # timer
        dt = 1.0 / self.control_rate
        self.timer = self.create_timer(dt, self._loop)

        self.get_logger().info("Ready. Waiting for /book_pose ...")

    # ======================== Params ========================

    # ======================== Params ========================

    def _load_params(self):
        self.alpha = self.get_parameter("alpha").value
        self.input_scale = self.get_parameter("input_scale").value
        self.k_joint1 = self.get_parameter("k_joint1").value
        self.k_vertical = self.get_parameter("k_vertical").value
        self.k_depth = self.get_parameter("k_depth").value
        self.k_pitch = self.get_parameter("k_pitch").value
        self.kd_dx = self.get_parameter("kd_dx").value
        self.kd_dy = self.get_parameter("kd_dy").value
        self.kd_pitch = self.get_parameter("kd_pitch").value
        self.kd_edge = self.get_parameter("kd_edge").value
        self.run_time = self.get_parameter("run_time").value
        self.control_rate = self.get_parameter("control_rate").value

        self.deadband_dx = self.get_parameter("deadband_dx").value
        self.deadband_dy = self.get_parameter("deadband_dy").value
        self.deadband_pitch = self.get_parameter("deadband_pitch").value
        self.deadband_edge = self.get_parameter("deadband_edge").value
        self.target_edge = self.get_parameter("target_edge").value

        self.min_move = self.get_parameter("min_move").value
        self.max_delta = self.get_parameter("max_delta").value
        self.max_dx = self.get_parameter("max_dx").value
        self.max_dy = self.get_parameter("max_dy").value
        self.max_pitch_err = self.get_parameter("max_pitch_err").value
        self.min_edge = self.get_parameter("min_edge").value
        self.max_edge = self.get_parameter("max_edge").value
        self.msg_timeout = self.get_parameter("msg_timeout").value

        self.j1_min = self.get_parameter("j1_min").value
        self.j1_max = self.get_parameter("j1_max").value
        self.j2_min = self.get_parameter("j2_min").value
        self.j2_max = self.get_parameter("j2_max").value
        self.j3_min = self.get_parameter("j3_min").value
        self.j3_max = self.get_parameter("j3_max").value
        self.j4_min = self.get_parameter("j4_min").value
        self.j4_max = self.get_parameter("j4_max").value
        self.j5_fixed = self.get_parameter("j5_fixed").value

    # ======================== Callback ========================

    def _cb(self, msg: BookPose):
        # validate input range — reject obviously bad frames
        if abs(msg.dx_px) > self.max_dx or abs(msg.dy_px) > self.max_dy:
            self.get_logger().warn("Reject frame: dx=%.0f dy=%.0f out of range" % (msg.dx_px, msg.dy_px))
            return
        if abs(msg.pitch_ratio - 1.0) > self.max_pitch_err:
            self.get_logger().warn("Reject frame: pitch=%.3f out of range" % msg.pitch_ratio)
            return
        if msg.avg_edge_px < self.min_edge or msg.avg_edge_px > self.max_edge:
            self.get_logger().warn("Reject frame: edge=%.0f out of range" % msg.avg_edge_px)
            return

        a = self.alpha
        self.filtered["dx"] = a * msg.dx_px + (1.0 - a) * self.filtered["dx"]
        self.filtered["dy"] = a * msg.dy_px + (1.0 - a) * self.filtered["dy"]
        self.filtered["pitch"] = a * msg.pitch_ratio + (1.0 - a) * self.filtered["pitch"]
        self.filtered["edge"] = a * msg.avg_edge_px + (1.0 - a) * self.filtered["edge"]
        self._msg_received = True
        self._last_msg_time = self.get_clock().now()

    # ======================== Control Loop ========================

    def _loop(self):
        if not self._msg_received:
            return

        # --- Lost target timeout: stop immediately ---
        now = self.get_clock().now()
        elapsed = (now - self._last_msg_time).nanoseconds * 1e-9
        if elapsed > self.msg_timeout:
            if self.mode != "LOST":
                self.get_logger().warn("Target lost (%.1fs no data), stopping" % elapsed)
                self.mode = "LOST"
                self._accum = [0.0, 0.0, 0.0, 0.0, 0.0]
            return

        dx = self.filtered["dx"]
        dy = self.filtered["dy"]
        pitch = self.filtered["pitch"]
        edge = self.filtered["edge"]

        # --- Stage determination ---
        if self.mode == "LOST":
            self.mode = "INIT"
        dx_ok = abs(dx) < self.deadband_dx
        dy_ok = abs(dy) < self.deadband_dy
        pitch_ok = abs(pitch - 1.0) < self.deadband_pitch
        edge_ok = abs(self.target_edge - edge) < self.deadband_edge

        if self.mode in ("INIT", "LOST", "STAGE3_IDLE"):
            if not dx_ok or not dy_ok:
                self.mode = "STAGE1_CENTER"
            elif not pitch_ok:
                self.mode = "STAGE2_PITCH"
            elif not edge_ok:
                self.mode = "STAGE3_DISTANCE"

        elif self.mode == "STAGE1_CENTER":
            if dx_ok and dy_ok:
                if not pitch_ok:
                    self.mode = "STAGE2_PITCH"
                elif not edge_ok:
                    self.mode = "STAGE3_DISTANCE"
                else:
                    self.mode = "STAGE3_IDLE"

        elif self.mode == "STAGE2_PITCH":
            if not dx_ok or not dy_ok:
                self.mode = "STAGE1_CENTER"
            elif pitch_ok:
                if not edge_ok:
                    self.mode = "STAGE3_DISTANCE"
                else:
                    self.mode = "STAGE3_IDLE"

        elif self.mode == "STAGE3_DISTANCE":
            if not dx_ok or not dy_ok:
                self.mode = "STAGE1_CENTER"
            elif not pitch_ok:
                self.mode = "STAGE2_PITCH"
            elif edge_ok:
                self.mode = "STAGE3_IDLE"

        # --- Compute deltas (PD: P + D) ---
        d = [0, 0, 0, 0, 0]

        if self.mode == "STAGE1_CENTER":
            # Joint1: horizontal — PD on dx_px
            dx_n = dx / 320.0
            dy_n = dy / 240.0
            dx_derr = 0.0 if self._prev_err["dx"] is None else (dx_n - self._prev_err["dx"])
            dy_derr = 0.0 if self._prev_err["dy"] is None else (dy_n - self._prev_err["dy"])
            self._prev_err["dx"] = dx_n
            self._prev_err["dy"] = dy_n

            raw1 = self.input_scale * (self.k_joint1 * dx_n + self.kd_dx * dx_derr)
            self._accum[0] += raw1
            d[0] = int(self._accum[0])
            self._accum[0] -= d[0]

            # Joints 2/3/4: vertical (平移模式) — PD on dy_px
            raw_v = self.input_scale * (self.k_vertical * dy_n + self.kd_dy * dy_derr)
            self._accum[1] += raw_v;      self._accum[2] += raw_v * 0.8; self._accum[3] += raw_v
            d[1] = int(self._accum[1]);    d[2] = int(self._accum[2]);   d[3] = int(self._accum[3])
            self._accum[1] -= d[1];        self._accum[2] -= d[2];       self._accum[3] -= d[3]

        elif self.mode == "STAGE2_PITCH":
            err = pitch - 1.0
            derr = 0.0 if self._prev_err["pitch"] is None else (err - self._prev_err["pitch"])
            self._prev_err["pitch"] = err

            raw_p = self.input_scale * (self.k_pitch * err + self.kd_pitch * derr)
            self._accum[1] += -raw_p;      self._accum[2] += raw_p;       self._accum[3] += -raw_p
            d[1] = int(self._accum[1]);    d[2] = int(self._accum[2]);    d[3] = int(self._accum[3])
            self._accum[1] -= d[1];        self._accum[2] -= d[2];        self._accum[3] -= d[3]

        elif self.mode == "STAGE3_DISTANCE":
            err = (self.target_edge - edge) / 500.0
            derr = 0.0 if self._prev_err["edge"] is None else (err - self._prev_err["edge"])
            self._prev_err["edge"] = err

            raw_d = self.input_scale * (self.k_depth * err + self.kd_edge * derr)
            self._accum[1] += -raw_d;      self._accum[2] += raw_d;       self._accum[3] += -raw_d * 0.5
            d[1] = int(self._accum[1]);    d[2] = int(self._accum[2]);    d[3] = int(self._accum[3])
            self._accum[1] -= d[1];        self._accum[2] -= d[2];        self._accum[3] -= d[3]

        # --- Step limit (anti-jerk) + min move (anti-jitter) ---
        for i in range(5):
            d[i] = max(-self.max_delta, min(self.max_delta, d[i]))
            if 0 < abs(d[i]) < self.min_move:
                self._accum[i] += d[i]  # return to accumulator
                d[i] = 0

        # --- Clamp + send ---
        # dynamic lower limits: J3/J4 ≥ 2000, J2<2000 → J3 ≥ 4000-J2
        j3_lo = max(self.j3_min, 2000)
        j4_lo = max(self.j4_min, 2000)
        j2_candidate = self.pulses[1] + d[1]
        if j2_candidate < 2000:
            j3_lo = max(j3_lo, 4000 - j2_candidate)

        limits = {
            1: (self.j1_min, self.j1_max),
            2: (self.j2_min, self.j2_max),
            3: (j3_lo, self.j3_max),
            4: (j4_lo, self.j4_max),
            5: (self.j5_fixed, self.j5_fixed),
        }
        if any(v != 0 for v in d):
            targets = []
            for i in range(5):
                if i == 4:
                    targets.append(self.j5_fixed)
                else:
                    lo, hi = limits[i + 1]
                    targets.append(max(lo, min(hi, self.pulses[i] + d[i])))

            for i in range(4):
                if targets[i] != self.pulses[i]:
                    self.car.set_uart_servo(i + 1, targets[i], run_time=self.run_time)

            self.pulses = targets
            self.get_logger().info(
                "[%s] dx=%.0f dy=%.0f pitch=%.3f edge=%.0f | d=%s | p=%s"
                % (self.mode, dx, dy, pitch, edge,
                   d, [int(p) for p in targets])
            )

        # publish state
        self.state_pub.publish(String(
            data="mode=%s dx=%.0f dy=%.0f pitch=%.3f edge=%.0f p=%s"
            % (self.mode, dx, dy, pitch, edge,
               [int(p) for p in self.pulses])
        ))

    def shutdown(self):
        """Release torque on Ctrl+C so arm can be moved manually."""
        self.get_logger().info("Shutting down — releasing torque...")
        try:
            self.car.set_uart_servo_torque(0)
            self.car.ser.close()
        except Exception:
            pass

    def __del__(self):
        if hasattr(self, "car"):
            try:
                self.car.set_uart_servo_torque(0)
            except Exception:
                pass
            try:
                self.car.ser.close()
            except Exception:
                pass


def main(args=None):
    rclpy.init(args=args)
    node = ArmController()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.shutdown()
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
