#!/usr/bin/env python3
"""Replacement for cartographer_occupancy_grid_node.
   Subscribes to /submap_list, queries each submap, publishes /map."""
import math
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, DurabilityPolicy
from nav_msgs.msg import OccupancyGrid
from cartographer_ros_msgs.msg import SubmapList
from cartographer_ros_msgs.srv import SubmapQuery
import numpy as np


class MapPublisher(Node):
    def __init__(self):
        super().__init__("map_publisher")
        self.declare_parameter("resolution", 0.05)
        self.res = self.get_parameter("resolution").value

        self.sub = self.create_subscription(SubmapList, "/submap_list", self.cb, 10)
        qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.pub = self.create_publisher(OccupancyGrid, "/map", qos)

        self.query_cli = self.create_client(SubmapQuery, "/submap_query")
        self._published = set()

        self.get_logger().info("MapPublisher ready — waiting for /submap_list...")

    def cb(self, msg):
        if len(msg.submap) == 0:
            return

        # Build combined occupancy grid from all finished submaps
        objs = []
        for sm in msg.submap:
            if not sm.is_frozen:
                continue
            sid = f"{sm.trajectory_id}_{sm.submap_index}"
            if sid in self._published:
                continue

            # Query submap grid
            if not self.query_cli.wait_for_service(timeout_sec=1.0):
                continue

            req = SubmapQuery.Request()
            req.trajectory_id = sm.trajectory_id
            req.submap_index = sm.submap_index
            future = self.query_cli.call_async(req)
            rclpy.spin_until_future_complete(self, future, timeout_sec=2.0)

            if not future.result():
                continue

            resp = future.result()
            if resp.status.code != 0:
                continue

            # Extract grid data
            w = resp.submap_version.width
            h = resp.submap_version.height
            cells = np.array(resp.submap_version.cells, dtype=np.int8).reshape(h, w)
            origin_x = resp.submap_version.resolution * resp.submap_version.slice_pose.index_x
            origin_y = resp.submap_version.resolution * resp.submap_version.slice_pose.index_y
            objs.append((cells, origin_x, origin_y, w, h, self.res))
            self._published.add(sid)

        if not objs:
            return

        # Publish combined map
        grid = OccupancyGrid()
        grid.header.frame_id = "map"
        grid.header.stamp = self.get_clock().now().to_msg()
        grid.info.resolution = self.res

        # Use the first submap as base
        grid.info.width = objs[0][3]
        grid.info.height = objs[0][4]
        grid.info.origin.position.x = objs[0][1]
        grid.info.origin.position.y = objs[0][2]
        grid.info.origin.orientation.w = 1.0

        combined = objs[0][0].astype(np.int8)
        # Overlay additional submaps
        for cells2, ox2, oy2, w2, h2, _ in objs[1:]:
            dx = int((ox2 - grid.info.origin.position.x) / self.res)
            dy = int((oy2 - grid.info.origin.position.y) / self.res)
            for y in range(h2):
                for x in range(w2):
                    nx = x + dx
                    ny = y + dy
                    if 0 <= nx < grid.info.width and 0 <= ny < grid.info.height:
                        v = int(cells2[y, x])
                        if v >= 0:  # known cell overwrites unknown
                            combined[ny, nx] = v

        grid.data = combined.flatten().tolist()
        self.pub.publish(grid)
        self.get_logger().info(
            f"/map {grid.info.width}x{grid.info.height} @ {self.res}m", throttle_duration_sec=5.0)


def main():
    rclpy.init()
    rclpy.spin(MapPublisher())
    rclpy.shutdown()

if __name__ == "__main__":
    main()
