"""摄像头操作 — 拍照。帧由 ROS2 侧 arm_agent 提供（cloud-model 不直接打开摄像头）。"""
import base64
import os
import time

import cv2
import numpy as np

from arm import agent_client


VLM_IMAGE_MAX_WIDTH = 640
VLM_JPEG_QUALITY = 95
READING_WAIT_TIMEOUT = 25.0
READING_WAIT_POLL_TIMEOUT = 1.0
PLATFORM_SNAPSHOT_WAIT_TIMEOUT = float(os.environ.get("VLM_PLATFORM_SNAPSHOT_WAIT_SEC", "2.0"))
PLATFORM_SNAPSHOT_POLL_TIMEOUT = float(os.environ.get("VLM_PLATFORM_SNAPSHOT_POLL_SEC", "0.1"))
_snapshot_provider = None


def set_snapshot_provider(provider):
    """Set platform-camera JPEG provider for normal VLM photos."""
    global _snapshot_provider
    _snapshot_provider = provider


def prepare_vlm_jpeg(jpg: bytes) -> bytes:
    """缩小送给云端 VLM 的图片；机械臂检测仍使用原始帧。"""
    frame = cv2.imdecode(
        np.frombuffer(jpg, dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )
    if frame is None:
        return b""

    height, width = frame.shape[:2]
    if width > VLM_IMAGE_MAX_WIDTH:
        target_height = max(
            1,
            round(height * VLM_IMAGE_MAX_WIDTH / width),
        )
        frame = cv2.resize(
            frame,
            (VLM_IMAGE_MAX_WIDTH, target_height),
            interpolation=cv2.INTER_AREA,
        )

    ok, encoded = cv2.imencode(
        ".jpg",
        frame,
        [cv2.IMWRITE_JPEG_QUALITY, VLM_JPEG_QUALITY],
    )
    return encoded.tobytes() if ok else b""


def capture(wait_ready: bool = False, cancel_event=None) -> str:
    """拍照并返回适合 VLM 的 base64 JPEG。返回空字符串表示失败。

    wait_ready=True（读书模式）: 等机械臂对齐书本且舵机静止后再取帧，保证非运动模糊。
    wait_ready=False（普通模式）: 立即取当前帧。
    """
    _raw_jpg, img_b64 = capture_raw_and_vlm(
        wait_ready=wait_ready,
        cancel_event=cancel_event,
    )
    return img_b64


def _get_frame_cancelable(wait_ready: bool, cancel_event,
                          wait_timeout: float, poll_timeout: float):
    if not wait_ready:
        if _snapshot_provider is None:
            return None
        deadline = time.time() + max(0.0, PLATFORM_SNAPSHOT_WAIT_TIMEOUT)
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return None
            try:
                jpg = _snapshot_provider()
            except Exception:
                jpg = None
            if jpg:
                return jpg
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            time.sleep(min(max(0.01, PLATFORM_SNAPSHOT_POLL_TIMEOUT), remaining))
    if cancel_event is None:
        return agent_client.get_frame(wait_ready=wait_ready)
    if cancel_event.is_set():
        return None

    deadline = time.time() + max(0.0, wait_timeout)
    while time.time() < deadline:
        if cancel_event.is_set():
            return None
        remaining = max(0.0, deadline - time.time())
        timeout = min(max(0.1, poll_timeout), remaining)
        jpg = agent_client.get_frame(wait_ready=True, timeout=timeout)
        if jpg:
            return jpg
    return None


def capture_raw_and_vlm(wait_ready: bool = False, cancel_event=None,
                        wait_timeout: float = READING_WAIT_TIMEOUT,
                        poll_timeout: float = READING_WAIT_POLL_TIMEOUT) -> tuple[bytes, str]:
    """Return the original camera JPEG plus the resized base64 image for VLM."""
    jpg = _get_frame_cancelable(
        wait_ready=wait_ready,
        cancel_event=cancel_event,
        wait_timeout=wait_timeout,
        poll_timeout=poll_timeout,
    )
    if not jpg:
        return b"", ""
    prepared = prepare_vlm_jpeg(jpg)
    if not prepared:
        return jpg, ""
    return jpg, base64.b64encode(prepared).decode()
