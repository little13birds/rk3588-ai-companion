#!/usr/bin/env python3
"""书本检测桥接节点: 定时 GET /json → /face_info

板端 live 二进制 (localhost:8080) 采集摄像头+NPU推理, 本节点轮询 /json 获取结果。
对齐 /align API 格式: 四边形中心+面积归一化。
与 face_detector.py 互换使用, 输出格式完全一致: [x, y, ratio, found]
"""

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32MultiArray
import json
import math
import requests


def polygon_area(pts):
    """Shoelace 公式计算四边形面积。pts: [[x,y], ...] 按序排列。"""
    n = len(pts)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        j = (i + 1) % n
        s += pts[i][0] * pts[j][1] - pts[j][0] * pts[i][1]
    return abs(s) / 2.0


class BookDetector(Node):
    def __init__(self):
        super().__init__('book_detector')

        self.declare_parameter('json_url', 'http://localhost:8080/json')
        self.declare_parameter('poll_rate', 10.0)
        self.declare_parameter('image_width', 3840)
        self.declare_parameter('image_height', 2160)
        self.declare_parameter('timeout', 1.0)

        self.face_info_pub = self.create_publisher(
            Float32MultiArray, '/face_info', 10,
        )

        rate = self.get_parameter('poll_rate').value
        self.timer = self.create_timer(1.0 / rate, self.poll)

        self.get_logger().info(
            f'BookDetector ready (poll @{rate:.0f}Hz): '
            f'{self.get_parameter("json_url").value}'
        )

    def poll(self):
        try:
            resp = requests.get(
                self.get_parameter('json_url').value,
                timeout=self.get_parameter('timeout').value,
            )
            resp.raise_for_status()
            text = resp.text.strip()
            if not text:
                return
            result = json.loads(text)
        except json.JSONDecodeError:
            return
        except requests.exceptions.Timeout:
            self.get_logger().warn('JSON timeout', throttle_duration_sec=1.0)
            return
        except requests.exceptions.ConnectionError:
            self.get_logger().warn(
                'live not running?', throttle_duration_sec=2.0
            )
            return
        except Exception as e:
            self.get_logger().warn(f'JSON error: {e}', throttle_duration_sec=1.0)
            return

        pages = result.get('pages', [])
        found = False
        x = y = ratio = 0.0

        for p in pages:
            corners = p.get('corners', {})
            pts = []
            for name in ('tl', 'tr', 'br', 'bl'):
                c = corners.get(name)
                if c is None or c[2] < 0.1:  # conf < 0.1 → 不可靠
                    continue
                # 跳过 NaN
                if math.isnan(c[0]) or math.isnan(c[1]):
                    continue
                pts.append([c[0], c[1]])

            if len(pts) < 3:
                continue

            # 过滤误检: 角点必须在图像范围内 (放宽容差)
            w = self.get_parameter('image_width').value
            h = self.get_parameter('image_height').value
            margin = 500
            if not all(-margin <= pt[0] <= w + margin and -margin <= pt[1] <= h + margin for pt in pts):
                continue

            # 四边形中心 (与 /align 一致: 所有有效角点平均)
            cx = sum(pt[0] for pt in pts) / len(pts)
            cy = sum(pt[1] for pt in pts) / len(pts)

            # 四边形面积 (Shoelace) / 图像总面积
            img_area = float(w) * float(h)
            quad_area = polygon_area(pts)
            if quad_area <= 0:
                continue

            x = cx / w
            y = cy / h
            ratio = quad_area / img_area
            found = True
            break

        info = Float32MultiArray()
        info.data = [float(x), float(y), float(ratio), 1.0 if found else 0.0]
        self.face_info_pub.publish(info)


def main():
    rclpy.init()
    node = BookDetector()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
