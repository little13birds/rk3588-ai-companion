#!/usr/bin/env python3
"""
Map preprocessor v3
- Obstacle: occupied black cells only (>= thresh)
- PCA per connected component → wall-aligned tight boxes
- Waypoints: box diagonal ends, extended 0.3m, merged within 0.5m
"""
import math, json, os
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, DurabilityPolicy
from nav_msgs.msg import OccupancyGrid
from visualization_msgs.msg import Marker
from geometry_msgs.msg import Point
import numpy as np


class MapPreprocessor(Node):
    def __init__(self):
        super().__init__("map_preprocessor")
        self.declare_parameter("safety_margin", 0.15)
        self.declare_parameter("wall_thickness", 0.50)
        self.declare_parameter("wp_extend", 0.30)
        self.declare_parameter("wp_merge_dist", 0.50)
        self.declare_parameter("min_obstacle_cells", 15)
        self.declare_parameter("obstacle_threshold", 50)
        self.declare_parameter("output_file",
                              os.path.expanduser("~/maps/waypoint_graph.json"))

        self.margin = self.get_parameter("safety_margin").value
        self.wall_t  = self.get_parameter("wall_thickness").value
        self.wp_ext  = self.get_parameter("wp_extend").value
        self.wp_mer  = self.get_parameter("wp_merge_dist").value
        self.min_c   = self.get_parameter("min_obstacle_cells").value
        self.obs_thr = self.get_parameter("obstacle_threshold").value
        self.outfile = self.get_parameter("output_file").value

        self.map_sub = self.create_subscription(OccupancyGrid, "/map", self.map_cb, 10)
        qos = QoSProfile(depth=10, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.marker_pub = self.create_publisher(Marker, "/waypoint_graph", qos)
        self.bbox_pub = self.create_publisher(Marker, "/obstacle_boxes", qos)
        self._done = False
        self.get_logger().info("Preprocessor v3 ready — waiting for /map...")

    # ======================== Main ========================

    def map_cb(self, msg):
        if self._done: return
        self._done = True
        info = msg.info
        h, w, r = info.height, info.width, info.resolution
        ox, oy = info.origin.position.x, info.origin.position.y
        data = np.array(msg.data, dtype=np.int8).reshape(h, w)
        self.get_logger().info(f"Map {w}x{h} r={r:.3f}")

        # 1. Black cells only
        mask = data >= self.obs_thr
        self.get_logger().info(f"  black cells: {np.sum(mask)}")

        # 2. Connected components
        regions = self._find_regions(mask)
        self.get_logger().info(f"  regions: {len(regions)}")

        # 3. OBB per region (PCA, tight along wall direction)
        boxes = []       # (cx,cy,angle,hw,hh) world
        raw_wps = []     # world coords before merge
        for pts in regions:
            if len(pts) < self.min_c: continue
            b, wps = self._obb_and_wps(pts, ox, oy, r, w, h, data)
            if b:
                boxes.append(b)
                raw_wps.extend(wps)
        self.get_logger().info(f"  boxes: {len(boxes)}, raw waypoints: {len(raw_wps)}")

        # 4. Merge close waypoints
        waypoints = self._merge_waypoints(raw_wps)
        self.get_logger().info(f"  after merge: {len(waypoints)}")

        # 5. Edges: MST + box enclosure rings
        wp_grid = [self._w2g(x, y, ox, oy, r) for x, y in waypoints]
        edges = self._build_mst(waypoints, wp_grid, data)
        edges += self._enclose_boxes(waypoints, boxes, wp_grid, data)
        # Deduplicate
        edges = list({(min(i, j), max(i, j)) for i, j in edges})
        self.get_logger().info(f"  edges: {len(edges)}")

        # 6. Save
        result = {
            "waypoints": [{"id": i, "x": waypoints[i][0], "y": waypoints[i][1]}
                          for i in range(len(waypoints))],
            "edges": edges,
        }
        with open(self.outfile, "w") as f:
            json.dump(result, f, indent=2)
        self.get_logger().info(f"  saved → {self.outfile}")

        # 7. Viz
        self._publish_viz(waypoints, edges, boxes)
        self.get_logger().info("Done. Ctrl+C to exit.")

    # ======================== OBB + Waypoints ========================

    def _obb_and_wps(self, grid_pts, ox, oy, r, w, h, data):
        """PCA-based OBB. Returns (box, [waypoints])."""
        # World coordinates
        xs = np.array([x * r + ox for _, x in grid_pts])
        ys = np.array([y * r + oy for y, _ in grid_pts])
        cx, cy = np.mean(xs), np.mean(ys)
        if len(xs) < 3: return None, []

        # PCA
        cov = np.cov(np.column_stack([xs - cx, ys - cy]).T)
        eigvals, eigvecs = np.linalg.eigh(cov)
        v1 = eigvecs[:, -1]    # principal (wall direction)
        v2 = eigvecs[:, 0]     # secondary (wall thickness)

        # Project
        proj1 = (xs - cx) * v1[0] + (ys - cy) * v1[1]
        proj2 = (xs - cx) * v2[0] + (ys - cy) * v2[1]

        # Half-extents: tight on secondary, margin only on principal
        half_w = (np.max(proj1) - np.min(proj1)) / 2.0 + self.margin
        half_h = self.wall_t / 2.0  # fixed wall thickness

        angle = math.atan2(v1[1], v1[0])
        ca, sa = math.cos(angle), math.sin(angle)

        # Box definition
        box = (cx, cy, angle, half_w, half_h)

        # Waypoints: 4 corners of expanded box
        expand = self.margin + self.wp_ext
        local_c = [(-half_w - expand, -half_h - expand),
                   ( half_w + expand, -half_h - expand),
                   ( half_w + expand,  half_h + expand),
                   (-half_w - expand,  half_h + expand)]
        wps = []
        for dx, dy in local_c:
            wx = cx + dx * ca - dy * sa
            wy = cy + dx * sa + dy * ca
            gx, gy = self._w2g(wx, wy, ox, oy, r)
            if self._is_free(gx, gy, w, h, data):
                wps.append((wx, wy))
        return box, wps

    # ======================== Waypoint Merge ========================

    def _merge_waypoints(self, wps):
        """Merge waypoints within wp_mer distance. Average their positions."""
        if not wps: return []
        used = [False] * len(wps)
        merged = []
        for i in range(len(wps)):
            if used[i]: continue
            cluster = [wps[i]]
            used[i] = True
            for j in range(i + 1, len(wps)):
                if used[j]: continue
                if math.hypot(wps[i][0] - wps[j][0], wps[i][1] - wps[j][1]) < self.wp_mer:
                    cluster.append(wps[j])
                    used[j] = True
            mx = sum(p[0] for p in cluster) / len(cluster)
            my = sum(p[1] for p in cluster) / len(cluster)
            merged.append((mx, my))
        return merged

    # ======================== Grid helpers ========================

    def _w2g(self, wx, wy, ox, oy, r):
        return (int((wx - ox) / r), int((wy - oy) / r))

    def _is_free(self, gx, gy, w, h, data):
        if gx < 0 or gx >= w or gy < 0 or gy >= h: return False
        v = int(data[gy, gx])
        return 0 <= v < self.obs_thr

    def _find_regions(self, mask):
        h, w = mask.shape
        visited = np.zeros((h, w), dtype=bool)
        regions = []
        D = [(-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)]
        for y in range(h):
            for x in range(w):
                if mask[y, x] and not visited[y, x]:
                    stack, reg = [(y, x)], []
                    while stack:
                        cy, cx = stack.pop()
                        if visited[cy, cx]: continue
                        visited[cy, cx] = True
                        reg.append((cy, cx))
                        for dy, dx in D:
                            ny, nx = cy + dy, cx + dx
                            if 0 <= ny < h and 0 <= nx < w:
                                if mask[ny, nx] and not visited[ny, nx]:
                                    stack.append((ny, nx))
                    if reg: regions.append(reg)
        return regions

    # ======================== MST (tree edges) ========================

    def _build_mst(self, wps, wp_grid, data):
        """Prim's algorithm: each node connects only to nearest clear-line neighbor."""
        n = len(wps)
        if n <= 1: return []
        # Build all valid edges with distances
        candidates = []
        for i in range(n):
            for j in range(i + 1, n):
                if self._line_clear(wp_grid[i], wp_grid[j], data):
                    d = math.hypot(wps[i][0] - wps[j][0], wps[i][1] - wps[j][1])
                    candidates.append((d, i, j))
        if not candidates: return []
        # Kruskal MST
        candidates.sort()
        parent = list(range(n))
        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb: parent[ra] = rb; return True
            return False
        edges = []
        for d, i, j in candidates:
            if union(i, j):
                edges.append((i, j))
        return edges

    def _enclose_boxes(self, wps, boxes, wp_grid, data):
        """For each obstacle box, connect nearby waypoints in a ring to enclose it."""
        extra = []
        for cx, cy, a, hw, hh in boxes:
            ca, sa = math.cos(a), math.sin(a)
            m = self.margin * 1.5
            local = [(-hw-m, -hh-m), (hw+m, -hh-m), (hw+m, hh+m), (-hw-m, hh+m)]
            corners_w = [(cx + dx*ca - dy*sa, cy + dx*sa + dy*ca) for dx, dy in local]
            ids = []
            for cwx, cwy in corners_w:
                best_i, best_d = -1, float("inf")
                for i, (wx, wy) in enumerate(wps):
                    d = math.hypot(cwx - wx, cwy - wy)
                    if d < best_d:
                        best_d, best_i = d, i
                if best_i >= 0 and best_i not in ids:
                    ids.append(best_i)
            if len(ids) >= 2:
                for p in range(len(ids)):
                    q = (p + 1) % len(ids)
                    if self._line_clear(wp_grid[ids[p]], wp_grid[ids[q]], data):
                        extra.append((ids[p], ids[q]))
        return extra

    def _line_clear(self, g1, g2, data):
        """Reject black (>=thr), gray (-1), out-of-bounds.
           Only allow known-free white cells (0..thr-1)."""
        x0, y0 = g1; x1, y1 = g2
        h, w = data.shape
        dx, dy = abs(x1-x0), -abs(y1-y0)
        sx, sy = (1 if x0<x1 else -1), (1 if y0<y1 else -1)
        err = dx + dy
        while True:
            if not (0 <= x0 < w and 0 <= y0 < h): return False
            v = int(data[y0, x0])
            if v < 0 or v >= self.obs_thr: return False  # gray or black
            if x0 == x1 and y0 == y1: break
            e2 = 2 * err
            if e2 >= dy: err += dy; x0 += sx
            if e2 <= dx: err += dx; y0 += sy
        return True
        x0, y0 = g1; x1, y1 = g2
        h, w = data.shape
        dx, dy = abs(x1-x0), -abs(y1-y0)
        sx, sy = (1 if x0<x1 else -1), (1 if y0<y1 else -1)
        err = dx + dy
        while True:
            if not (0 <= x0 < w and 0 <= y0 < h): return False
            if int(data[y0, x0]) >= self.obs_thr: return False
            if x0 == x1 and y0 == y1: break
            e2 = 2 * err
            if e2 >= dy: err += dy; x0 += sx
            if e2 <= dx: err += dx; y0 += sy
        return True

    # ======================== Visualization ========================

    def _publish_viz(self, wps, edges, boxes):
        now = self.get_clock().now().to_msg()
        # --- Edges (green) ---
        me = Marker()
        me.header.frame_id = "map"; me.header.stamp = now
        me.ns = "wp_graph"; me.id = 0; me.type = Marker.LINE_LIST
        me.scale.x = 0.015; me.color.g = 1.0; me.color.a = 0.5
        for i, j in edges:
            me.points.append(Point(x=wps[i][0], y=wps[i][1]))
            me.points.append(Point(x=wps[j][0], y=wps[j][1]))
        self.marker_pub.publish(me)
        # --- Waypoints (blue) ---
        mw = Marker()
        mw.header.frame_id = "map"; mw.header.stamp = now
        mw.ns = "wp_graph"; mw.id = 1; mw.type = Marker.SPHERE_LIST
        mw.scale.x = mw.scale.y = mw.scale.z = 0.08
        mw.color.b = 1.0; mw.color.a = 0.9
        for x, y in wps: mw.points.append(Point(x=x, y=y))
        self.marker_pub.publish(mw)
        # --- Boxes (red wireframe) ---
        mb = Marker()
        mb.header.frame_id = "map"; mb.header.stamp = now
        mb.ns = "obs_boxes"; mb.id = 0; mb.type = Marker.LINE_LIST
        mb.scale.x = 0.02; mb.color.r = 1.0; mb.color.a = 0.7
        for cx, cy, a, hw, hh in boxes:
            ca, sa = math.cos(a), math.sin(a)
            c = [(cx + dx*ca - dy*sa, cy + dx*sa + dy*ca)
                 for dx, dy in [(-hw,-hh),(hw,-hh),(hw,hh),(-hw,hh)]]
            for p, q in [(0,1),(1,2),(2,3),(3,0)]:
                mb.points.append(Point(x=c[p][0], y=c[p][1]))
                mb.points.append(Point(x=c[q][0], y=c[q][1]))
        self.bbox_pub.publish(mb)


def main():
    rclpy.init()
    rclpy.spin(MapPreprocessor())
    rclpy.shutdown()

if __name__ == "__main__":
    main()
