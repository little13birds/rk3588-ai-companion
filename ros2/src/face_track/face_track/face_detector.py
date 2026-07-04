#!/usr/bin/env python3
"""OpenCV Haar Cascade 人脸检测节点: 订阅图像 → 检测人脸 → 发布中心坐标+面积比例"""

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from std_msgs.msg import Float32MultiArray, String
import cv2
from cv_bridge import CvBridge


class FaceDetector(Node):
    def __init__(self):
        super().__init__('face_detector')

        self.declare_parameter('image_topic', '/image_raw')
        self.declare_parameter('publish_annotated', True)
        self.declare_parameter('scale_factor', 1.1)
        self.declare_parameter('min_neighbors', 5)
        self.declare_parameter('min_size_w', 60)
        self.declare_parameter('min_size_h', 60)

        self.bridge = CvBridge()

        # OpenCV 内置 Haar Cascade（无需下载文件）
        cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        self.face_cascade = cv2.CascadeClassifier(cascade_path)
        if self.face_cascade.empty():
            self.get_logger().error(f'Failed to load cascade: {cascade_path}')
            raise RuntimeError('Cascade load failed')
        self.get_logger().info(f'FaceDetector ready (Haar Cascade)\n  {cascade_path}')

        self.image_sub = self.create_subscription(
            Image, self.get_parameter('image_topic').value,
            self.image_callback, 10
        )
        self.face_info_pub = self.create_publisher(
            Float32MultiArray, '/face_info', 10
        )
        self.status_pub = self.create_publisher(
            String, '/face_status', 10
        )
        if self.get_parameter('publish_annotated').value:
            self.annotated_pub = self.create_publisher(
                Image, '/face_annotated', 10
            )

    def image_callback(self, msg: Image):
        try:
            img = self.bridge.imgmsg_to_cv2(msg, 'bgr8')
        except Exception as e:
            self.get_logger().error(f'CvBridge error: {e}')
            return

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]

        sf = self.get_parameter('scale_factor').value
        mn = self.get_parameter('min_neighbors').value
        ms = (self.get_parameter('min_size_w').value, self.get_parameter('min_size_h').value)

        faces = self.face_cascade.detectMultiScale(gray, sf, mn, minSize=ms)

        face_x = 0.0
        face_y = 0.0
        face_ratio = 0.0
        found = len(faces) > 0

        if found:
            # 取面积最大的人脸
            best = max(faces, key=lambda r: r[2] * r[3])
            x, y, fw, fh = best
            face_x = (x + fw / 2.0) / w
            face_y = (y + fh / 2.0) / h
            face_ratio = (fw * fh) / (w * h)

            if self.get_parameter('publish_annotated').value:
                cv2.rectangle(img, (x, y), (x + fw, y + fh), (0, 255, 0), 2)
                cx, cy = int(face_x * w), int(face_y * h)
                cv2.circle(img, (cx, cy), 4, (0, 0, 255), -1)
                cv2.line(img, (w // 2, 0), (w // 2, h), (255, 255, 0), 1)
                cv2.line(img, (0, h // 2), (w, h // 2), (255, 255, 0), 1)

        if self.get_parameter('publish_annotated').value:
            annotated = self.bridge.cv2_to_imgmsg(img, 'bgr8')
            annotated.header = msg.header
            self.annotated_pub.publish(annotated)

        info = Float32MultiArray()
        info.data = [float(face_x), float(face_y), float(face_ratio), 1.0 if found else 0.0]
        self.face_info_pub.publish(info)

        if found:
            self.status_pub.publish(
                String(data=f'FACE: x={face_x:.3f} y={face_y:.3f} r={face_ratio:.3f}')
            )
        else:
            self.status_pub.publish(String(data='NO_FACE'))


def main():
    rclpy.init()
    node = FaceDetector()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
