#!/usr/bin/env python3
"""
书本视觉伺服桥接: ctypes → libbook_detect.so → 归一化 → /face_info
替换 face_detector + camera_publisher, servo_controller 无需改动

用法:
    ros2 run face_track book_servo_bridge
"""
import ctypes
import json
import os
import sys

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32MultiArray


LIB_PATH = os.path.expanduser("~/book_detect/build/libbook_detect.so")
MODEL_PATH = os.path.expanduser("~/book_detect/model/best_hybrid_v9.rknn")

_lib = ctypes.CDLL(LIB_PATH)
_lib.book_detect_init.argtypes = [ctypes.c_char_p]
_lib.book_detect_init.restype = ctypes.c_void_p
_lib.book_detect_release.argtypes = [ctypes.c_void_p]
_lib.book_detect_release.restype = None
_lib.book_detect_infer.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_ubyte),
    ctypes.c_int,
]
_lib.book_detect_infer.restype = ctypes.c_void_p

_libc = ctypes.CDLL(None)
_libc.free.argtypes = [ctypes.c_void_p]
_libc.free.restype = None


class BookServoBridge(Node):
    def __init__(self):
        super().__init__("book_servo_bridge")

        self.declare_parameter("model_path", MODEL_PATH)
        self.declare_parameter("camera_index", 21)
        self.declare_parameter("width", 1280)
        self.declare_parameter("height", 720)
        self.declare_parameter("jpeg_quality", 95)
        self.declare_parameter("ratio_scale", 3.0)

        model = self.get_parameter("model_path").value
        self.model_handle = _lib.book_detect_init(model.encode())
        if not self.model_handle:
            self.get_logger().fatal(f"Failed to load model: {model}")
            sys.exit(1)
        self.get_logger().info(f"Model loaded: {model}")

        # 自动检测 USB 摄像头 (通过 sysfs name，应对重枚举)
        usb_idx = -1
        for idx in range(32):
            name_path = f"/sys/class/video4linux/video{idx}/name"
            if not os.path.exists(name_path):
                continue
            dev_name = open(name_path).read().strip()
            if "USB Camera" not in dev_name:
                continue
            test_cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
            if test_cap.isOpened():
                usb_idx = idx
                self.get_logger().info(
                    f"Auto-detected USB camera: /dev/video{idx} ({dev_name})"
                )
                test_cap.release()
                break
            test_cap.release()

        cam_idx = self.get_parameter("camera_index").value
        if usb_idx >= 0:
            cam_idx = usb_idx

        self.w = self.get_parameter("width").value
        self.h = self.get_parameter("height").value
        self.q = self.get_parameter("jpeg_quality").value

        self.cap = cv2.VideoCapture(cam_idx, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            self.get_logger().fatal(f"Cannot open camera /dev/video{cam_idx}")
            raise RuntimeError(f"Cannot open camera /dev/video{cam_idx}")

        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.w)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.h)
        self.cap.set(cv2.CAP_PROP_FPS, 30)

        actual_w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.get_logger().info(f"Camera ready: /dev/video{cam_idx} {actual_w}x{actual_h}")

        self.pub = self.create_publisher(Float32MultiArray, "/face_info", 10)
        self.timer = self.create_timer(1.0 / 60.0, self.tick)
        self.frame_cnt = 0
        self.detect_cnt = 0

        self.get_logger().info("BookServoBridge ready")

    def tick(self):
        ret, frame = self.cap.read()
        if not ret:
            return

        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.q])
        if not ok:
            return

        self.frame_cnt += 1

        buf = (ctypes.c_ubyte * len(jpg)).from_buffer_copy(jpg.tobytes())
        ptr = _lib.book_detect_infer(self.model_handle, buf, len(jpg))
        if not ptr:
            return

        try:
            result = json.loads(ctypes.cast(ptr, ctypes.c_char_p).value)
        except json.JSONDecodeError:
            _libc.free(ptr)
            return

        _libc.free(ptr)

        if result.get("found"):
            cx = result["center"][0] / self.w
            cy = result["center"][1] / self.h
            ar = result.get("area_ratio", 0.0) * self.get_parameter("ratio_scale").value
            msg = Float32MultiArray(data=[float(cx), float(cy), float(ar), 1.0])
            self.pub.publish(msg)
            self.detect_cnt += 1
        else:
            msg = Float32MultiArray(data=[0.0, 0.0, 0.0, 0.0])
            self.pub.publish(msg)

        if self.frame_cnt % 100 == 0:
            self.get_logger().info(
                f"frames: {self.frame_cnt}, detections: {self.detect_cnt}"
            )

    def __del__(self):
        if hasattr(self, "cap"):
            self.cap.release()
        if hasattr(self, "model_handle") and self.model_handle:
            _lib.book_detect_release(self.model_handle)


def main():
    rclpy.init()
    node = BookServoBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
