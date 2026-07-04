#!/usr/bin/env python3
"""
Waypoint-graph navigator: loads preprocessed graph, Dijkstra + fixed-speed control.
Subs: /goal_pose, /tf   Pubs: /cmd_vel, /nav_path (Marker)
"""
import math
import json
import os
import heapq
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist, PoseStamped, Point
from visualization_msgs.msg import Marker
import tf2_ros


class WaypointNavigator(Node):
    def __init__(self):
        super().__init__("waypoint_navigator")
        self.declare_parameter("linear_speed", 0.15)
        self.declare_parameter("angular_speed", 0.5)
        self.declare_parameter("wp_tolerance", 0.20)
        self.declare_parameter("graph_file",
                               os.path.expanduser("~/maps/waypoint_graph.json"))

        self.linear_speed = self.get_parameter("linear_speed").value
        self.angular_speed = self.get_parameter("angular_speed").value
        self.wp_tolerance = self.get_parameter("wp_tolerance").value

        # Load graph
        gfile = self.get_parameter("graph_file").value
        self.graph = self._load_graph(gfile)
        if not self.graph:
            self.get_logger().fatal(f"Cannot load {gfile}. Run preprocess_map first.")
            raise RuntimeError("No waypoint graph")

        self.wps = self.graph["waypoints"]
        self.edges = self.graph["edges"]
        self.adj = self._build_adj()
        self.get_logger().info(f"Loaded {len(self.wps)} waypoints, {len(self.edges)} edges")

        # Subscribers
        self.goal_sub = self.create_subscription(PoseStamped, "/goal_pose", self.goal_cb, 10)

        # Publishers
        self.cmd_pub = self.create_publisher(Twist, "/cmd_vel", 10)
        self.path_pub = self.create_publisher(Marker, "/nav_path", 10)

        # TF
        self.tf_buffer = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self)

        # State
        self.path = []       # [(x,y), ...]
        self.wp_idx = 0
        self.state = "IDLE"
        self.goal = None
        self._last_tf_time = 0.0
        self._cached_pose = None
        self._last_cmd = Twist()

        self.timer = self.create_timer(0.1, self.control_loop)

    # ======================== Graph Load ========================

    def _load_graph(self, path):
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return json.load(f)

    def _build_adj(self):
        adj = {i: [] for i in range(len(self.wps))}
        for i, j in self.edges:
            adj[i].append(j)
            adj[j].append(i)
        return adj

    def _nearest_wp(self, x, y):
        best_id, best_d = -1, float("inf")
        for wp in self.wps:
            d = math.hypot(wp["x"] - x, wp["y"] - y)
            if d < best_d:
                best_d, best_id = d, wp["id"]
        return best_id

    # ======================== Dijkstra ========================

    def _dijkstra(self, start_id, goal_id):
        dist = {start_id: 0.0}
        prev = {start_id: None}
        pq = [(0.0, start_id)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, float("inf")):
                continue
            if u == goal_id:
                break
            for v in self.adj[u]:
                wx, wy = self.wps[v]["x"], self.wps[v]["y"]
                ux, uy = self.wps[u]["x"], self.wps[u]["y"]
                nd = d + math.hypot(wx - ux, wy - uy)
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))
        # Reconstruct
        if goal_id not in prev:
            return None
        path = []
        u = goal_id
        while u is not None:
            path.append((self.wps[u]["x"], self.wps[u]["y"]))
            u = prev[u]
        path.reverse()
        return path

    # ======================== Callbacks ========================

    def goal_cb(self, msg):
        self.goal = (msg.pose.position.x, msg.pose.position.y)
        self.get_logger().info(f"Goal: ({self.goal[0]:.2f}, {self.goal[1]:.2f})")
        self._plan()

    def _plan(self):
        pose = self._get_pose()
        if pose is None:
            self.get_logger().warn("No pose — need 2D Pose Estimate in rviz2")
            return
        sx, sy = pose[0], pose[1]
        gx, gy = self.goal
        swp = self._nearest_wp(sx, sy)
        gwp = self._nearest_wp(gx, gy)
        if swp == gwp:
            self.path = [(sx, sy), (gx, gy)]
        else:
            wp_path = self._dijkstra(swp, gwp)
            if wp_path is None:
                self.get_logger().warn("No path in waypoint graph")
                return
            self.path = [(sx, sy)] + wp_path + [(gx, gy)]
        self.wp_idx = 0
        self.state = "MOVING"
        self._publish_path()
        self.get_logger().info(f"Path: {len(self.path)} waypoints")

    # ======================== Pose ========================

    def _get_pose(self):
        now = self.get_clock().now().nanoseconds * 1e-9
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

    # ======================== Controller ========================

    def control_loop(self):
        if self.state != "MOVING" or not self.path:
            return
        pose = self._get_pose()
        if pose is None:
            return
        cx, cy, cyaw = pose
        tx, ty = self.path[self.wp_idx]
        dx, dy = tx - cx, ty - cy
        dist = math.hypot(dx, dy)
        ta = math.atan2(dy, dx)
        a_err = ta - cyaw
        while a_err > math.pi:
            a_err -= 2 * math.pi
        while a_err < -math.pi:
            a_err += 2 * math.pi

        cmd = Twist()
        is_last = self.wp_idx == len(self.path) - 1

        if dist < self.wp_tolerance:
            if is_last:
                cmd.linear.x = 0.0
                cmd.angular.z = 0.0
                self.cmd_pub.publish(cmd)
                self._last_cmd = cmd
                self.state = "IDLE"
                self.get_logger().info("GOAL REACHED")
                return
            self.wp_idx += 1
            return

        if abs(a_err) > 0.12:
            cmd.angular.z = self.angular_speed if a_err > 0 else -self.angular_speed
        else:
            cmd.linear.x = self.linear_speed * (1.0 - abs(a_err) / 0.5)
            cmd.linear.x = max(cmd.linear.x, 0.03)
            cmd.angular.z = a_err * 1.5

        self.cmd_pub.publish(cmd)
        self._last_cmd = cmd

    # ======================== Visualization ========================

    def _publish_path(self):
        m = Marker()
        m.header.frame_id = "map"
        m.header.stamp = self.get_clock().now().to_msg()
        m.ns = "nav"
        m.id = 0
        m.type = Marker.LINE_STRIP
        m.scale.x = 0.04
        m.color.r = 1.0
        m.color.g = 0.8
        m.color.a = 1.0
        for x, y in self.path:
            m.points.append(Point(x=x, y=y))
        self.path_pub.publish(m)


def main():
    rclpy.init()
    rclpy.spin(WaypointNavigator())
    rclpy.shutdown()


if __name__ == "__main__":
    main()
