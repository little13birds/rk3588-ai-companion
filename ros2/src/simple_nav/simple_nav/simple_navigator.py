#!/usr/bin/env python3
"""
Simple Bug2 Planner + Fixed-speed Controller
Subs: /map, /goal_pose, /tf
Pubs: /cmd_vel, /waypoints (Marker)
"""
import math
import rclpy
from rclpy.node import Node
from nav_msgs.msg import OccupancyGrid
from geometry_msgs.msg import Twist, PoseStamped, Point
from visualization_msgs.msg import Marker
import tf2_ros
import numpy as np


class SimpleNavigator(Node):
    def __init__(self):
        super().__init__("simple_navigator")

        # --- Params ---
        self.declare_parameter("linear_speed", 0.15)
        self.declare_parameter("angular_speed", 0.5)
        self.declare_parameter("waypoint_tolerance", 0.15)
        self.declare_parameter("goal_tolerance", 0.20)
        self.declare_parameter("obstacle_threshold", 50)

        self.linear_speed = self.get_parameter("linear_speed").value
        self.angular_speed = self.get_parameter("angular_speed").value
        self.waypoint_tolerance = self.get_parameter("waypoint_tolerance").value
        self.goal_tolerance = self.get_parameter("goal_tolerance").value
        self.obstacle_threshold = self.get_parameter("obstacle_threshold").value

        # --- Subscribers ---
        self.map_sub = self.create_subscription(OccupancyGrid, "/map", self.map_cb, 10)
        self.goal_sub = self.create_subscription(PoseStamped, "/goal_pose", self.goal_cb, 10)

        # --- Publishers ---
        self.cmd_pub = self.create_publisher(Twist, "/cmd_vel", 10)
        self.marker_pub = self.create_publisher(Marker, "/waypoints", 10)

        # --- TF ---
        self.tf_buffer = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self)

        # --- State ---
        self.map_data = None
        self.map_info = None
        self.waypoints = []
        self.wp_idx = 0
        self.goal = None
        self.state = "IDLE"

        # --- TF cache (only lookup every 0.5s) ---
        self._last_tf_time = 0.0
        self._cached_pose = None
        self._last_cmd = Twist()          # for dead reckoning

        # --- Control loop ---
        self.timer = self.create_timer(0.1, self.control_loop)

    # ======================== Callbacks ========================

    def map_cb(self, msg):
        # Only update if map actually changed
        t = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        if hasattr(self, "_map_stamp") and self._map_stamp == t:
            return
        self._map_stamp = t
        h, w = msg.info.height, msg.info.width
        self.map_data = np.array(msg.data, dtype=np.int8).reshape(h, w)
        self.map_info = msg.info

    def goal_cb(self, msg):
        self.goal = (msg.pose.position.x, msg.pose.position.y)
        self.get_logger().info(f"Goal received: ({self.goal[0]:.2f}, {self.goal[1]:.2f})")
        self.plan_and_start()

    # ======================== Pose (cached + dead reckoning) ========================

    def get_pose(self):
        now = self.get_clock().now().nanoseconds * 1e-9
        # TF lookup only every 0.5s; between lookups use dead reckoning
        if self._cached_pose is None or now - self._last_tf_time > 0.5:
            try:
                t = self.tf_buffer.lookup_transform("map", "base_link", rclpy.time.Time())
                x = t.transform.translation.x
                y = t.transform.translation.y
                r = t.transform.rotation
                siny = 2.0 * (r.w * r.z + r.x * r.y)
                cosy = 1.0 - 2.0 * (r.y * r.y + r.z * r.z)
                yaw = math.atan2(siny, cosy)
                self._cached_pose = (x, y, yaw)
                self._last_tf_time = now
                return (x, y, yaw)
            except Exception:
                return self._cached_pose
        else:
            # Dead reckon from last pose + last cmd_vel
            dt = now - self._last_tf_time
            x, y, yaw = self._cached_pose
            v = self._last_cmd.linear.x
            w = self._last_cmd.angular.z
            if abs(w) > 0.01:
                x += v / w * (math.sin(yaw + w * dt) - math.sin(yaw))
                y -= v / w * (math.cos(yaw + w * dt) - math.cos(yaw))
                yaw += w * dt
            else:
                x += v * math.cos(yaw) * dt
                y += v * math.sin(yaw) * dt
            return (x, y, yaw)

    # ======================== Grid helpers ========================

    def _world_to_grid(self, wx, wy):
        if self.map_info is None:
            return None
        gx = int((wx - self.map_info.origin.position.x) / self.map_info.resolution)
        gy = int((wy - self.map_info.origin.position.y) / self.map_info.resolution)
        if 0 <= gx < self.map_info.width and 0 <= gy < self.map_info.height:
            return (gx, gy)
        return None

    def _is_free_at_world(self, wx, wy):
        """True if cell at world coords is free (not occupied)"""
        g = self._world_to_grid(wx, wy)
        if g is None or self.map_data is None:
            return False
        gx, gy = g
        return int(self.map_data[gy, gx]) < self.obstacle_threshold

    def line_is_clear(self, p1, p2):
        """Bresenham line check: True if no obstacle cells between p1 and p2"""
        g1 = self._world_to_grid(p1[0], p1[1])
        g2 = self._world_to_grid(p2[0], p2[1])
        if g1 is None or g2 is None:
            return False
        x0, y0 = g1
        x1, y1 = g2
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            if x0 < 0 or x0 >= self.map_info.width or y0 < 0 or y0 >= self.map_info.height:
                return False
            if int(self.map_data[y0, x0]) >= self.obstacle_threshold:
                return False
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy
        return True

    # ======================== Bug2 Planner ========================

    def plan_and_start(self):
        if self.map_data is None:
            self.get_logger().warn("No map yet")
            return
        pose = self.get_pose()
        if pose is None:
            self.get_logger().warn("No pose yet, need 2D Pose Estimate in rviz2")
            return

        start = (pose[0], pose[1])
        goal = self.goal
        self.get_logger().info(f"Planning: ({start[0]:.2f},{start[1]:.2f}) -> ({goal[0]:.2f},{goal[1]:.2f})")

        waypoints = self._bug2(start, goal)
        if waypoints:
            self.waypoints = waypoints
            self.wp_idx = 0
            self.state = "MOVING"
            self._publish_markers()
            self.get_logger().info(f"Path: {len(waypoints)} waypoints")
        else:
            self.get_logger().warn("Planning failed")

    def _bug2(self, start, goal):
        """Bug2: move along m-line, follow obstacle when blocked"""
        waypoints = [start]
        pos = start
        m_dir = math.atan2(goal[1] - start[1], goal[0] - start[0])
        res = self.map_info.resolution
        step = res * 2                         # step size per iteration
        hit_point = None
        max_iter = 5000

        for i in range(max_iter):
            if math.hypot(goal[0] - pos[0], goal[1] - pos[1]) < self.goal_tolerance:
                waypoints.append(goal)
                return self._simplify(waypoints)

            # Try to go straight toward goal
            target_dir = math.atan2(goal[1] - pos[1], goal[0] - pos[0])
            nx = pos[0] + math.cos(target_dir) * step
            ny = pos[1] + math.sin(target_dir) * step

            if self._is_free_at_world(nx, ny):
                pos = (nx, ny)
                if hit_point:                        # left obstacle
                    waypoints.append(pos)
                    hit_point = None
                continue
            else:
                # Hit obstacle: follow boundary (left-hand rule)
                if hit_point is None:
                    hit_point = pos
                    waypoints.append(pos)

                # Search around current position for a free cell
                pos = self._follow_boundary(pos, goal, step, res)
                if pos is None:
                    self.get_logger().warn("Stuck, no path")
                    waypoints.append(goal)
                    return self._simplify(waypoints)

        waypoints.append(goal)
        return self._simplify(waypoints)

    def _follow_boundary(self, pos, goal, step, res):
        """Follow obstacle boundary, trying to get closer to goal.
        Checks free cells in concentric arcs, picks the one closest to goal
        whose line-of-sight to goal is clear (leave condition)."""
        best = None
        best_dist = float("inf")
        angles = 36

        for i in range(angles):
            a = (i * 2 * math.pi / angles) - math.pi
            cx = pos[0] + math.cos(a) * step
            cy = pos[1] + math.sin(a) * step

            if not self._is_free_at_world(cx, cy):
                continue

            d = math.hypot(goal[0] - cx, goal[1] - cy)
            if d < best_dist and self.line_is_clear((cx, cy), goal):
                best = (cx, cy)
                best_dist = d

        if best:
            return best

        # No leave point found, just follow wall
        for i in range(angles):
            a = (i * 2 * math.pi / angles) - math.pi
            cx = pos[0] + math.cos(a) * step
            cy = pos[1] + math.sin(a) * step
            if self._is_free_at_world(cx, cy):
                return (cx, cy)

        return None

    def _simplify(self, wps):
        """Remove redundant waypoints (collinear within tolerance)"""
        if len(wps) <= 2:
            return wps
        simple = [wps[0]]
        for i in range(1, len(wps) - 1):
            if not self.line_is_clear(simple[-1], wps[i + 1]):
                simple.append(wps[i])
        simple.append(wps[-1])
        return simple

    # ======================== Controller ========================

    def control_loop(self):
        if self.state != "MOVING" or not self.waypoints:
            return

        pose = self.get_pose()
        if pose is None:
            return

        cx, cy, cyaw = pose
        tx, ty = self.waypoints[self.wp_idx]
        dx, dy = tx - cx, ty - cy
        dist = math.hypot(dx, dy)
        target_a = math.atan2(dy, dx)

        # Angle error
        a_err = target_a - cyaw
        while a_err > math.pi:
            a_err -= 2 * math.pi
        while a_err < -math.pi:
            a_err += 2 * math.pi

        cmd = Twist()
        is_last = self.wp_idx == len(self.waypoints) - 1
        tol = self.goal_tolerance if is_last else self.waypoint_tolerance

        if dist < tol:
            if is_last:
                cmd.linear.x = 0.0
                cmd.angular.z = 0.0
                self.cmd_pub.publish(cmd)
                self._last_cmd = cmd
                self.state = "IDLE"
                self.get_logger().info("GOAL REACHED")
                return
            else:
                self.wp_idx += 1
                return

        if abs(a_err) > 0.12:
            # Rotate in place
            cmd.angular.z = self.angular_speed if a_err > 0 else -self.angular_speed
        else:
            # Move forward + minor angular correction
            cmd.linear.x = self.linear_speed * (1.0 - abs(a_err) / 0.5)
            cmd.linear.x = max(cmd.linear.x, 0.03)
            cmd.angular.z = a_err * 1.5

        self.cmd_pub.publish(cmd)
        self._last_cmd = cmd

    # ======================== Visualization ========================

    def _publish_markers(self):
        now = self.get_clock().now().to_msg()
        # Path line
        m = Marker()
        m.header.frame_id = "map"
        m.header.stamp = now
        m.ns = "path"
        m.id = 0
        m.type = Marker.LINE_STRIP
        m.scale.x = 0.03
        m.color.g = 1.0
        m.color.a = 1.0
        for wp in self.waypoints:
            m.points.append(Point(x=wp[0], y=wp[1]))
        self.marker_pub.publish(m)

        # Goal sphere
        g = Marker()
        g.header.frame_id = "map"
        g.header.stamp = now
        g.ns = "goal"
        g.id = 0
        g.type = Marker.SPHERE
        g.scale.x = g.scale.y = g.scale.z = 0.2
        g.color.r = 1.0
        g.color.a = 1.0
        g.pose.position.x = self.goal[0]
        g.pose.position.y = self.goal[1]
        self.marker_pub.publish(g)


def main():
    rclpy.init()
    rclpy.spin(SimpleNavigator())
    rclpy.shutdown()


if __name__ == "__main__":
    main()
