from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

import cv2
import rclpy
from cv_bridge import CvBridge
from rclpy.node import Node
from sensor_msgs.msg import CameraInfo, Image

from .depth_utils import estimate_bbox_depth_m, nearest_detection
from .person_detector import Detection, create_person_detector
from .person_speed_monitor import CameraModel, PersonObservation, PersonSpeedMonitor
from .web_monitor_utils import FpsCounter, MonitorStatus, choose_box_color


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>深度相机网页监控</title>
  <style>
    :root {
      color-scheme: dark;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #0f1418;
      color: #e8f1f2;
    }
    body {
      margin: 0;
      min-height: 100vh;
      background: #0f1418;
    }
    main {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 280px;
      gap: 16px;
      padding: 16px;
      box-sizing: border-box;
      min-height: 100vh;
    }
    .video {
      background: #111b20;
      border: 1px solid #26343b;
      border-radius: 6px;
      overflow: hidden;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 420px;
    }
    .video img {
      width: 100%;
      height: 100%;
      object-fit: contain;
      display: block;
    }
    aside {
      background: #152027;
      border: 1px solid #26343b;
      border-radius: 6px;
      padding: 14px;
      box-sizing: border-box;
    }
    h1 {
      font-size: 18px;
      margin: 0 0 12px;
      font-weight: 650;
    }
    .state {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 14px;
      padding: 5px 8px;
      border-radius: 4px;
      background: #183326;
      color: #7df0a0;
      font-weight: 650;
    }
    .state.fast {
      background: #3a1e20;
      color: #ff7a73;
    }
    .row {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      padding: 7px 0;
      border-bottom: 1px solid #25323a;
      font-size: 14px;
    }
    .row span:first-child {
      color: #9fb0b7;
    }
    .row span:last-child {
      color: #ffffff;
      font-variant-numeric: tabular-nums;
      text-align: right;
    }
    @media (max-width: 820px) {
      main {
        grid-template-columns: 1fr;
      }
      .video {
        min-height: 260px;
      }
    }
  </style>
</head>
<body>
  <main>
    <section class="video">
      <img src="/stream.mjpg" alt="深度相机推理画面">
    </section>
    <aside>
      <h1>深度相机监控</h1>
      <div id="state" class="state">等待数据</div>
      <div class="row"><span>相机 FPS</span><span id="camera_fps">0.00</span></div>
      <div class="row"><span>推理 FPS</span><span id="inference_fps">0.00</span></div>
      <div class="row"><span>网页 FPS</span><span id="stream_fps">0.00</span></div>
      <div class="row"><span>分辨率</span><span id="resolution">0x0</span></div>
      <div class="row"><span>检测人数</span><span id="person_count">0</span></div>
      <div class="row"><span>最近距离</span><span id="distance">--</span></div>
      <div class="row"><span>目标速度</span><span id="speed">--</span></div>
      <div class="row"><span>速度阈值</span><span id="threshold">--</span></div>
    </aside>
  </main>
  <script>
    async function refreshStatus() {
      try {
        const res = await fetch('/status.json', { cache: 'no-store' });
        const data = await res.json();
        const state = document.getElementById('state');
        state.textContent = data.alert.message || 'OK';
        state.className = data.alert.fast_active || data.alert.active ? 'state fast' : 'state';
        document.getElementById('camera_fps').textContent = data.fps.camera.toFixed(2);
        document.getElementById('inference_fps').textContent = data.fps.inference.toFixed(2);
        document.getElementById('stream_fps').textContent = data.fps.stream.toFixed(2);
        document.getElementById('resolution').textContent = data.image.width + 'x' + data.image.height;
        document.getElementById('person_count').textContent = data.people.count;
        document.getElementById('distance').textContent =
          data.people.nearest_distance_m === null ? '--' : data.people.nearest_distance_m.toFixed(2) + ' m';
        document.getElementById('speed').textContent =
          data.people.nearest_speed_mps === null ? '--' : data.people.nearest_speed_mps.toFixed(2) + ' m/s';
        document.getElementById('threshold').textContent = data.alert.threshold_mps.toFixed(2) + ' m/s';
      } catch (err) {
        document.getElementById('state').textContent = '连接中断';
        document.getElementById('state').className = 'state fast';
      }
    }
    setInterval(refreshStatus, 500);
    refreshStatus();
  </script>
</body>
</html>
"""


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def _clamp(value: float, min_value: int, max_value: int) -> int:
    return int(max(min_value, min(max_value, round(value))))


def _draw_label(frame, x: int, y: int, text: str, color) -> None:
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.5
    thickness = 1
    (text_w, text_h), baseline = cv2.getTextSize(text, font, scale, thickness)
    top = max(0, y - text_h - baseline - 6)
    cv2.rectangle(frame, (x, top), (x + text_w + 8, top + text_h + baseline + 6), color, -1)
    cv2.putText(frame, text, (x + 4, top + text_h + 2), font, scale, (10, 18, 20), thickness, cv2.LINE_AA)


class PersonWebMonitorNode(Node):
    def __init__(self):
        super().__init__("person_web_monitor")
        self.declare_parameter("color_topic", "/camera/color/image_raw")
        self.declare_parameter("depth_topic", "/camera/depth/image_raw")
        self.declare_parameter("camera_info_topic", "/camera/color/camera_info")
        self.declare_parameter("detector_backend", "ultralytics")
        self.declare_parameter("model_path", "/home/elf/ros2/yolov8n.pt")
        self.declare_parameter("confidence", 0.4)
        self.declare_parameter("roi_fraction", 0.5)
        self.declare_parameter("speed_threshold_mps", 1.5)
        self.declare_parameter("duration_threshold_s", 1.0)
        self.declare_parameter("alert_cooldown_s", 5.0)
        self.declare_parameter("max_sample_gap_s", 0.75)
        self.declare_parameter("process_period_sec", 0.2)
        self.declare_parameter("log_period_sec", 1.0)
        self.declare_parameter("web_host", "0.0.0.0")
        self.declare_parameter("web_port", 8088)
        self.declare_parameter("jpeg_quality", 80)
        self.declare_parameter("stream_period_sec", 0.2)
        self.declare_parameter("alert_hold_sec", 1.5)

        self._bridge = CvBridge()
        self._image_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._latest_color: Optional[Image] = None
        self._latest_depth: Optional[Image] = None
        self._camera_model: Optional[CameraModel] = None
        self._latest_jpeg: Optional[bytes] = None
        self._last_process_s = 0.0
        self._last_log_s = 0.0
        self._alert_until_s = 0.0
        self._camera_fps = FpsCounter()
        self._inference_fps = FpsCounter()
        self._stream_fps = FpsCounter()
        self._status = self._empty_status("WAITING")
        self._server: Optional[ReusableThreadingHTTPServer] = None
        self._server_thread: Optional[threading.Thread] = None

        backend = str(self.get_parameter("detector_backend").value)
        model_path = str(self.get_parameter("model_path").value)
        confidence = float(self.get_parameter("confidence").value)
        self._speed_threshold_mps = float(self.get_parameter("speed_threshold_mps").value)
        self._detector = create_person_detector(backend, model_path, confidence)
        self._monitor = PersonSpeedMonitor(
            speed_threshold_mps=self._speed_threshold_mps,
            duration_threshold_s=float(self.get_parameter("duration_threshold_s").value),
            alert_cooldown_s=float(self.get_parameter("alert_cooldown_s").value),
            max_sample_gap_s=float(self.get_parameter("max_sample_gap_s").value),
        )

        color_topic = str(self.get_parameter("color_topic").value)
        depth_topic = str(self.get_parameter("depth_topic").value)
        camera_info_topic = str(self.get_parameter("camera_info_topic").value)
        self.create_subscription(Image, color_topic, self._on_color, 10)
        self.create_subscription(Image, depth_topic, self._on_depth, 10)
        self.create_subscription(CameraInfo, camera_info_topic, self._on_camera_info, 10)
        self.create_timer(0.05, self._tick)
        self._start_web_server()
        self.get_logger().info(
            "person web monitor listening color=%s depth=%s camera_info=%s"
            % (color_topic, depth_topic, camera_info_topic)
        )

    def _empty_status(self, message: str) -> MonitorStatus:
        now = time.monotonic()
        return MonitorStatus(
            camera_fps=0.0,
            inference_fps=0.0,
            stream_fps=0.0,
            image_width=0,
            image_height=0,
            person_count=0,
            nearest_distance_m=None,
            nearest_speed_mps=None,
            speed_threshold_mps=float(self.get_parameter("speed_threshold_mps").value)
            if self.has_parameter("speed_threshold_mps")
            else 1.5,
            fast_active=False,
            alert_active=False,
            last_update_s=now,
            message=message,
        )

    def _on_color(self, msg: Image) -> None:
        now = time.monotonic()
        with self._image_lock:
            self._latest_color = msg
        with self._state_lock:
            self._camera_fps.mark(now)

    def _on_depth(self, msg: Image) -> None:
        with self._image_lock:
            self._latest_depth = msg

    def _on_camera_info(self, msg: CameraInfo) -> None:
        if msg.k[0] <= 0.0 or msg.k[4] <= 0.0:
            return
        with self._image_lock:
            self._camera_model = CameraModel(
                width=int(msg.width),
                height=int(msg.height),
                fx=float(msg.k[0]),
                fy=float(msg.k[4]),
                cx=float(msg.k[2]),
                cy=float(msg.k[5]),
            )

    def _tick(self) -> None:
        now = time.monotonic()
        process_period = float(self.get_parameter("process_period_sec").value)
        if now - self._last_process_s < process_period:
            return
        self._last_process_s = now
        self._process_latest(now)

    def _process_latest(self, now: float) -> None:
        with self._image_lock:
            color_msg = self._latest_color
            depth_msg = self._latest_depth
            camera_model = self._camera_model
        if color_msg is None:
            self._update_status(self._empty_status("等待彩色图"))
            return

        color = self._bridge.imgmsg_to_cv2(color_msg, desired_encoding="bgr8")
        if depth_msg is None:
            frame = color.copy()
            self._draw_banner(frame, "WAITING DEPTH")
            self._publish_frame_and_status(frame, self._empty_status("等待深度图"), now)
            return

        depth = self._bridge.imgmsg_to_cv2(depth_msg, desired_encoding="passthrough")
        camera = camera_model or CameraModel.approximate(width=color.shape[1], height=color.shape[0])
        roi_fraction = float(self.get_parameter("roi_fraction").value)

        detections = []
        for detection in self._detector.detect(color):
            distance_m = estimate_bbox_depth_m(depth, detection.bbox, fraction=roi_fraction)
            detections.append(detection.with_distance(distance_m))

        nearest = nearest_detection(detections)
        speed_mps = None
        fast_active = False
        alert_active = False
        if nearest is None or nearest.distance_m is None:
            self._monitor.reset()
            message = "未检测到人" if not detections else "等待有效深度"
        else:
            observation = PersonObservation(
                timestamp_s=now,
                bbox=nearest.bbox,
                confidence=nearest.confidence,
                distance_m=nearest.distance_m,
                camera=camera,
                track_id=nearest.track_id,
            )
            result = self._monitor.update(observation)
            speed_mps = result.speed_mps
            fast_active = speed_mps is not None and speed_mps > self._speed_threshold_mps
            if result.alert_triggered:
                self._alert_until_s = now + float(self.get_parameter("alert_hold_sec").value)
            alert_active = result.alert_triggered or now < self._alert_until_s
            message = "FAST" if fast_active or alert_active else "OK"

        frame = color.copy()
        for detection in detections:
            is_nearest = detection is nearest
            self._draw_detection(frame, detection, is_nearest, speed_mps, fast_active or alert_active)

        if not detections:
            self._draw_banner(frame, "NO PERSON")

        status = self._make_status(
            now=now,
            width=color.shape[1],
            height=color.shape[0],
            person_count=len(detections),
            nearest_distance_m=None if nearest is None else nearest.distance_m,
            nearest_speed_mps=speed_mps,
            fast_active=fast_active,
            alert_active=alert_active,
            message=message,
        )
        self._publish_frame_and_status(frame, status, now)
        self._log_periodic(status)

    def _draw_detection(
        self,
        frame,
        detection: Detection,
        is_nearest: bool,
        speed_mps: Optional[float],
        nearest_fast: bool,
    ) -> None:
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = detection.bbox
        left = _clamp(x1, 0, width - 1)
        top = _clamp(y1, 0, height - 1)
        right = _clamp(x2, 0, width - 1)
        bottom = _clamp(y2, 0, height - 1)
        has_depth = detection.distance_m is not None
        color = choose_box_color(is_fast=is_nearest and nearest_fast, has_depth=has_depth)
        cv2.rectangle(frame, (left, top), (right, bottom), color, 2)

        parts = ["person %.2f" % detection.confidence]
        if detection.distance_m is not None:
            parts.append("%.2fm" % detection.distance_m)
        if is_nearest and speed_mps is not None:
            parts.append("%.2fm/s" % speed_mps)
        if is_nearest and nearest_fast:
            parts.insert(0, "FAST")
        _draw_label(frame, left, top, " ".join(parts), color)

    def _draw_banner(self, frame, text: str) -> None:
        cv2.rectangle(frame, (0, 0), (frame.shape[1], 34), (20, 30, 35), -1)
        cv2.putText(
            frame,
            text,
            (10, 23),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (230, 240, 240),
            2,
            cv2.LINE_AA,
        )

    def _make_status(
        self,
        now: float,
        width: int,
        height: int,
        person_count: int,
        nearest_distance_m: Optional[float],
        nearest_speed_mps: Optional[float],
        fast_active: bool,
        alert_active: bool,
        message: str,
    ) -> MonitorStatus:
        with self._state_lock:
            self._inference_fps.mark(now)
            return MonitorStatus(
                camera_fps=self._camera_fps.fps(now),
                inference_fps=self._inference_fps.fps(now),
                stream_fps=self._stream_fps.fps(now),
                image_width=width,
                image_height=height,
                person_count=person_count,
                nearest_distance_m=nearest_distance_m,
                nearest_speed_mps=nearest_speed_mps,
                speed_threshold_mps=self._speed_threshold_mps,
                fast_active=fast_active,
                alert_active=alert_active,
                last_update_s=now,
                message=message,
            )

    def _publish_frame_and_status(self, frame, status: MonitorStatus, now: float) -> None:
        quality = int(self.get_parameter("jpeg_quality").value)
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if not ok:
            self.get_logger().warn("failed to encode annotated frame")
            return
        with self._state_lock:
            self._latest_jpeg = encoded.tobytes()
            self._status = status

    def _update_status(self, status: MonitorStatus) -> None:
        with self._state_lock:
            self._status = status

    def _log_periodic(self, status: MonitorStatus) -> None:
        now = time.monotonic()
        period = float(self.get_parameter("log_period_sec").value)
        if now - self._last_log_s < period:
            return
        self._last_log_s = now
        self.get_logger().info(
            "web persons=%d distance=%s speed=%s camera_fps=%.2f infer_fps=%.2f alert=%s"
            % (
                status.person_count,
                "none" if status.nearest_distance_m is None else "%.2fm" % status.nearest_distance_m,
                "none" if status.nearest_speed_mps is None else "%.2fm/s" % status.nearest_speed_mps,
                status.camera_fps,
                status.inference_fps,
                status.alert_active or status.fast_active,
            )
        )

    def _start_web_server(self) -> None:
        host = str(self.get_parameter("web_host").value)
        port = int(self.get_parameter("web_port").value)
        node = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path == "/" or self.path.startswith("/?"):
                    self._send_html(INDEX_HTML)
                elif self.path.startswith("/status.json"):
                    self._send_json(node.get_status_json())
                elif self.path.startswith("/snapshot.jpg"):
                    self._send_snapshot()
                elif self.path.startswith("/stream.mjpg"):
                    self._send_stream()
                else:
                    self.send_error(404, "not found")

            def _send_html(self, html: str) -> None:
                data = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _send_json(self, data: str) -> None:
                raw = data.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _send_snapshot(self) -> None:
                jpeg = node.get_latest_jpeg()
                if jpeg is None:
                    self.send_error(503, "frame not ready")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(jpeg)))
                self.end_headers()
                self.wfile.write(jpeg)

            def _send_stream(self) -> None:
                self.send_response(200)
                self.send_header("Age", "0")
                self.send_header("Cache-Control", "no-cache, private")
                self.send_header("Pragma", "no-cache")
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                period = float(node.get_parameter("stream_period_sec").value)
                while rclpy.ok():
                    jpeg = node.get_latest_jpeg()
                    if jpeg is not None:
                        try:
                            node.mark_stream_frame()
                            self.wfile.write(b"--frame\r\n")
                            self.wfile.write(b"Content-Type: image/jpeg\r\n")
                            self.wfile.write(("Content-Length: %d\r\n\r\n" % len(jpeg)).encode("ascii"))
                            self.wfile.write(jpeg)
                            self.wfile.write(b"\r\n")
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            break
                    time.sleep(max(0.03, period))

            def log_message(self, format, *args):  # noqa: A002,N802
                return

        self._server = ReusableThreadingHTTPServer((host, port), Handler)
        self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._server_thread.start()
        self.get_logger().info("web monitor available at http://%s:%d" % (host, port))

    def get_latest_jpeg(self) -> Optional[bytes]:
        with self._state_lock:
            return self._latest_jpeg

    def get_status_json(self) -> str:
        with self._state_lock:
            return self._status.to_json()

    def mark_stream_frame(self) -> None:
        now = time.monotonic()
        with self._state_lock:
            self._stream_fps.mark(now)
            self._status = replace(self._status, stream_fps=self._stream_fps.fps(now))

    def stop_web_server(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        if self._server_thread is not None:
            self._server_thread.join(timeout=2.0)
            self._server_thread = None

    def destroy_node(self):
        self.stop_web_server()
        return super().destroy_node()


def main() -> None:
    rclpy.init()
    node = PersonWebMonitorNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
