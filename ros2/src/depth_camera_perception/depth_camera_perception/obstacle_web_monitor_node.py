from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

import cv2
import numpy as np
import rclpy
from cv_bridge import CvBridge
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from sensor_msgs.msg import Image
from std_msgs.msg import String

from .depth_utils import normalize_depth_to_meters
from .obstacle_avoidance import ObstacleConfig
from .obstacle_web_utils import (
    ObstacleWebStatus,
    obstacle_level,
    parse_guard_status,
    status_payload,
    waiting_status,
)
from .web_monitor_utils import FpsCounter


FRONT_ZONE_WIDTH_FRACTION = ObstacleConfig().front_width_fraction


INDEX_HTML = '''<!doctype html>
<html lang='zh-CN'>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <title>深度避障监控</title>
  <style>
    :root {
      color-scheme: dark;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif;
      background: #101417;
      color: #edf3f4;
    }
    body { margin: 0; min-height: 100vh; background: #101417; }
    main {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 320px;
      gap: 14px;
      padding: 14px;
      min-height: 100vh;
      box-sizing: border-box;
    }
    .video {
      border: 1px solid #26343b;
      background: #11191d;
      border-radius: 6px;
      overflow: hidden;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 420px;
    }
    .video img { width: 100%; height: 100%; object-fit: contain; display: block; }
    aside {
      border: 1px solid #26343b;
      background: #162027;
      border-radius: 6px;
      padding: 14px;
      box-sizing: border-box;
    }
    h1 { margin: 0 0 12px; font-size: 18px; font-weight: 680; }
    .state {
      display: inline-flex;
      padding: 6px 9px;
      border-radius: 4px;
      margin-bottom: 12px;
      background: #183326;
      color: #79f0a0;
      font-weight: 700;
    }
    .state.danger { background: #3b2020; color: #ff8378; }
    .state.waiting { background: #34301d; color: #ffd56e; }
    .row {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      padding: 7px 0;
      border-bottom: 1px solid #27343b;
      font-size: 14px;
    }
    .row span:first-child { color: #9fb2b9; }
    .row span:last-child { color: #fff; text-align: right; font-variant-numeric: tabular-nums; }
    @media (max-width: 850px) {
      main { grid-template-columns: 1fr; }
      .video { min-height: 260px; }
    }
  </style>
</head>
<body>
  <main>
    <section class='video'><img src='/stream.mjpg' alt='深度避障画面'></section>
    <aside>
      <h1>深度避障监控</h1>
      <div id='state' class='state waiting'>等待数据</div>
      <div class='row'><span>运行模式</span><span id='mode'>--</span></div>
      <div class='row'><span>前方距离</span><span id='front'>--</span></div>
      <div class='row'><span>左侧距离</span><span id='left'>--</span></div>
      <div class='row'><span>右侧距离</span><span id='right'>--</span></div>
      <div class='row'><span>前方无效深度</span><span id='front_invalid'>--</span></div>
      <div class='row'><span>左侧无效深度</span><span id='left_invalid'>--</span></div>
      <div class='row'><span>右侧无效深度</span><span id='right_invalid'>--</span></div>
      <div class='row'><span>输出线速度</span><span id='vx'>--</span></div>
      <div class='row'><span>输出角速度</span><span id='wz'>--</span></div>
      <div class='row'><span>避障阶段</span><span id='phase'>--</span></div>
      <div class='row'><span>绕行距离</span><span id='bypass_distance'>--</span></div>
      <div class='row'><span>航向误差</span><span id='yaw_error'>--</span></div>
      <div class='row'><span>跟随侧</span><span id='tracked_side'>--</span></div>
      <div class='row'><span>前进偏移</span><span id='forward_offset'>--</span></div>
      <div class='row'><span>横向偏移</span><span id='lateral_offset'>--</span></div>
      <div class='row'><span>原航向误差</span><span id='heading_error'>--</span></div>
      <div class='row'><span>找回转角</span><span id='reacquire_turn'>--</span></div>
      <div class='row'><span>位姿保护</span><span id='pose_state'>--</span></div>
      <div class='row'><span>深度延迟</span><span id='depth_age'>--</span></div>
      <div class='row'><span>位姿延迟</span><span id='pose_age'>--</span></div>
      <div class='row'><span>指令延迟</span><span id='cmd_age'>--</span></div>
      <div class='row'><span>相机 FPS</span><span id='camera_fps'>0.00</span></div>
      <div class='row'><span>深度 FPS</span><span id='depth_fps'>0.00</span></div>
      <div class='row'><span>网页 FPS</span><span id='stream_fps'>0.00</span></div>
      <div class='row'><span>分辨率</span><span id='resolution'>0x0</span></div>
    </aside>
  </main>
  <script>
    function meters(value) {
      return value === null ? '--' : value.toFixed(2) + ' m';
    }
    function seconds(value) {
      return value === null ? '--' : value.toFixed(2) + ' s';
    }
    function percent(value) {
      return value === null ? '--' : Math.round(value * 100) + '%';
    }
    function radians(value) {
      return value === null ? '--' : value.toFixed(3) + ' rad';
    }
    async function refreshStatus() {
      try {
        const res = await fetch('/status.json', { cache: 'no-store' });
        const data = await res.json();
        const state = document.getElementById('state');
        state.textContent = data.state.message;
        state.className = 'state ' + data.state.level;
        document.getElementById('mode').textContent = data.mode.dry_run ? 'DRY-RUN' : 'REAL';
        document.getElementById('front').textContent = meters(data.zones.front.distance_m);
        document.getElementById('left').textContent = meters(data.zones.left.distance_m);
        document.getElementById('right').textContent = meters(data.zones.right.distance_m);
        document.getElementById('front_invalid').textContent = percent(data.zones.front.invalid_fraction);
        document.getElementById('left_invalid').textContent = percent(data.zones.left.invalid_fraction);
        document.getElementById('right_invalid').textContent = percent(data.zones.right.invalid_fraction);
        document.getElementById('vx').textContent = data.output.linear_x.toFixed(3) + ' m/s';
        document.getElementById('wz').textContent = data.output.angular_z.toFixed(3) + ' rad/s';
        document.getElementById('phase').textContent = data.avoidance.phase || '--';
        document.getElementById('bypass_distance').textContent = meters(data.avoidance.bypass_distance_m);
        document.getElementById('yaw_error').textContent = radians(data.avoidance.yaw_error_rad);
        document.getElementById('tracked_side').textContent = data.avoidance.tracked_side || '--';
        document.getElementById('forward_offset').textContent = meters(data.avoidance.forward_offset_m);
        document.getElementById('lateral_offset').textContent = meters(data.avoidance.lateral_offset_m);
        document.getElementById('heading_error').textContent = radians(data.avoidance.heading_error_rad);
        document.getElementById('reacquire_turn').textContent = radians(data.avoidance.reacquire_turn_rad);
        document.getElementById('pose_state').textContent = data.avoidance.pose_stalled ? '位姿无变化' : '正常';
        document.getElementById('depth_age').textContent = seconds(data.age.depth_s);
        document.getElementById('pose_age').textContent = seconds(data.age.pose_s);
        document.getElementById('cmd_age').textContent = seconds(data.age.cmd_s);
        document.getElementById('camera_fps').textContent = data.fps.camera.toFixed(2);
        document.getElementById('depth_fps').textContent = data.fps.depth.toFixed(2);
        document.getElementById('stream_fps').textContent = data.fps.stream.toFixed(2);
        document.getElementById('resolution').textContent = data.image.width + 'x' + data.image.height;
      } catch (err) {
        const state = document.getElementById('state');
        state.textContent = '连接中断';
        state.className = 'state danger';
      }
    }
    setInterval(refreshStatus, 500);
    refreshStatus();
  </script>
</body>
</html>
'''


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class ObstacleWebMonitorNode(Node):
    def __init__(self):
        super().__init__('obstacle_web_monitor')
        self.declare_parameter('image_topic', '/camera/color/image_raw')
        self.declare_parameter('depth_topic', '/camera/depth/image_raw')
        self.declare_parameter('status_topic', '/depth_camera/obstacle_status')
        self.declare_parameter('image_encoding', 'bgr8')
        self.declare_parameter('web_host', '0.0.0.0')
        self.declare_parameter('web_port', 8090)
        self.declare_parameter('jpeg_quality', 80)
        self.declare_parameter('stream_period_sec', 0.2)
        self.declare_parameter('process_period_sec', 0.1)

        self._bridge = CvBridge()
        self._image_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._latest_image: Optional[Image] = None
        self._latest_depth: Optional[Image] = None
        self._latest_jpeg: Optional[bytes] = None
        self._last_process_s = 0.0
        now = time.monotonic()
        self._status = waiting_status('waiting', now)
        self._camera_fps = FpsCounter()
        self._depth_fps = FpsCounter()
        self._stream_fps = FpsCounter()
        self._image_width = 0
        self._image_height = 0
        self._server: Optional[ReusableThreadingHTTPServer] = None
        self._server_thread: Optional[threading.Thread] = None

        image_topic = str(self.get_parameter('image_topic').value)
        depth_topic = str(self.get_parameter('depth_topic').value)
        status_topic = str(self.get_parameter('status_topic').value)
        self.create_subscription(Image, image_topic, self._on_image, 10)
        self.create_subscription(Image, depth_topic, self._on_depth, 10)
        self.create_subscription(String, status_topic, self._on_status, 10)
        self.create_timer(0.05, self._tick)
        self._start_web_server()
        self.get_logger().info(
            'obstacle web monitor listening image=%s depth=%s status=%s'
            % (image_topic, depth_topic, status_topic)
        )

    def _on_image(self, msg: Image) -> None:
        now = time.monotonic()
        with self._image_lock:
            self._latest_image = msg
        with self._state_lock:
            self._camera_fps.mark(now)

    def _on_depth(self, msg: Image) -> None:
        now = time.monotonic()
        with self._image_lock:
            self._latest_depth = msg
        with self._state_lock:
            self._depth_fps.mark(now)

    def _on_status(self, msg: String) -> None:
        status = parse_guard_status(msg.data, time.monotonic())
        with self._state_lock:
            self._status = status

    def _tick(self) -> None:
        now = time.monotonic()
        period = float(self.get_parameter('process_period_sec').value)
        if now - self._last_process_s < period:
            return
        self._last_process_s = now

        with self._image_lock:
            image_msg = self._latest_image
            depth_msg = self._latest_depth
        with self._state_lock:
            status = self._status

        frame = self._frame_from_inputs(image_msg, depth_msg, status)
        quality = int(self.get_parameter('jpeg_quality').value)
        ok, encoded = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if not ok:
            self.get_logger().warn('failed to encode obstacle web frame')
            return
        with self._state_lock:
            self._latest_jpeg = encoded.tobytes()
            self._image_height, self._image_width = frame.shape[:2]

    def _frame_from_inputs(
        self,
        image_msg: Optional[Image],
        depth_msg: Optional[Image],
        status: ObstacleWebStatus,
    ):
        color_frame = self._frame_from_image(image_msg, status)
        depth_frame = self._frame_from_depth(depth_msg, color_frame.shape[1], color_frame.shape[0])
        self._draw_zone_overlay(color_frame, status)
        self._draw_zone_overlay(depth_frame, status)
        frame = np.hstack((color_frame, depth_frame))
        self._draw_banner(frame, status)
        return frame

    def _frame_from_image(self, image_msg: Optional[Image], status: ObstacleWebStatus):
        if image_msg is None:
            return _placeholder_frame(status)
        try:
            encoding = str(self.get_parameter('image_encoding').value)
            frame = self._bridge.imgmsg_to_cv2(image_msg, desired_encoding=encoding)
        except Exception as exc:
            self.get_logger().warn('failed to convert image for web monitor: %s' % exc)
            return _placeholder_frame(status)
        if frame.ndim == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        return frame.copy()

    def _frame_from_depth(self, depth_msg: Optional[Image], width: int, height: int):
        if depth_msg is None:
            return _placeholder_depth_frame(width, height)
        try:
            depth = self._bridge.imgmsg_to_cv2(depth_msg, desired_encoding='passthrough')
        except Exception as exc:
            self.get_logger().warn('failed to convert depth for web monitor: %s' % exc)
            return _placeholder_depth_frame(width, height)
        return _depth_to_bgr(depth, width=width, height=height)

    def _draw_overlay(self, frame, status: ObstacleWebStatus) -> None:
        ObstacleWebMonitorNode._draw_zone_overlay(self, frame, status)
        ObstacleWebMonitorNode._draw_banner(self, frame, status)

    def _draw_zone_overlay(self, frame, status: ObstacleWebStatus) -> None:
        height, width = frame.shape[:2]
        front_left, front_right = _front_zone_bounds(width)
        overlay = frame.copy()
        zones = [
            (0, front_left, _zone_color(status.left_clear, False), '左侧 %s' % _distance_text(status.left_distance_m)),
            (front_left, front_right, _zone_color(not status.front_blocked, status.front_blocked), '前方 %s' % _distance_text(status.front_distance_m)),
            (front_right, width, _zone_color(status.right_clear, False), '右侧 %s' % _distance_text(status.right_distance_m)),
        ]
        for x1, x2, color, _label in zones:
            cv2.rectangle(overlay, (x1, 0), (x2, height), color, -1)
        cv2.addWeighted(overlay, 0.22, frame, 0.78, 0, frame)
        for x1, x2, color, label in zones:
            cv2.rectangle(frame, (x1, 0), (x2, height - 1), color, 2)
            _draw_label(frame, max(4, x1 + 8), 40, label, color)

    def _draw_banner(self, frame, status: ObstacleWebStatus) -> None:
        height, width = frame.shape[:2]
        level = obstacle_level(status)
        banner_color = (40, 150, 70)
        if level == 'danger':
            banner_color = (35, 35, 230)
        elif level == 'waiting':
            banner_color = (40, 160, 210)
        mode = 'DRY-RUN' if status.dry_run else 'REAL'
        banner = '%s | %s | vx %.3f m/s wz %.3f rad/s' % (
            mode,
            level.upper(),
            status.output_linear_x,
            status.output_angular_z,
        )
        cv2.rectangle(frame, (0, 0), (width, 32), banner_color, -1)
        cv2.putText(frame, banner, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (245, 250, 250), 2, cv2.LINE_AA)

    def _start_web_server(self) -> None:
        host = str(self.get_parameter('web_host').value)
        port = int(self.get_parameter('web_port').value)
        node = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path == '/' or self.path.startswith('/?'):
                    self._send_html(INDEX_HTML)
                elif self.path.startswith('/status.json'):
                    self._send_json(node.get_status_json())
                elif self.path.startswith('/snapshot.jpg'):
                    self._send_snapshot()
                elif self.path.startswith('/stream.mjpg'):
                    self._send_stream()
                else:
                    self.send_error(404, 'not found')

            def _send_html(self, html: str) -> None:
                data = html.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _send_json(self, data: str) -> None:
                raw = data.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _send_snapshot(self) -> None:
                jpeg = node.get_latest_jpeg()
                if jpeg is None:
                    self.send_error(503, 'frame not ready')
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(jpeg)))
                self.end_headers()
                self.wfile.write(jpeg)

            def _send_stream(self) -> None:
                self.send_response(200)
                self.send_header('Age', '0')
                self.send_header('Cache-Control', 'no-cache, private')
                self.send_header('Pragma', 'no-cache')
                self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
                self.end_headers()
                period = float(node.get_parameter('stream_period_sec').value)
                while rclpy.ok():
                    jpeg = node.get_latest_jpeg()
                    if jpeg is not None:
                        try:
                            node.mark_stream_frame()
                            self.wfile.write(b'--frame\r\n')
                            self.wfile.write(b'Content-Type: image/jpeg\r\n')
                            self.wfile.write(('Content-Length: %d\r\n\r\n' % len(jpeg)).encode('ascii'))
                            self.wfile.write(jpeg)
                            self.wfile.write(b'\r\n')
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            break
                    time.sleep(max(0.03, period))

            def log_message(self, format, *args):  # noqa: A002,N802
                return

        self._server = ReusableThreadingHTTPServer((host, port), Handler)
        self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._server_thread.start()
        self.get_logger().info('obstacle web monitor available at http://%s:%d' % (host, port))

    def get_latest_jpeg(self) -> Optional[bytes]:
        with self._state_lock:
            return self._latest_jpeg

    def get_status_json(self) -> str:
        with self._state_lock:
            payload = status_payload(
                self._status,
                camera_fps=self._camera_fps.fps(time.monotonic()),
                depth_fps=self._depth_fps.fps(time.monotonic()),
                stream_fps=self._stream_fps.fps(time.monotonic()),
                image_width=self._image_width,
                image_height=self._image_height,
            )
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def mark_stream_frame(self) -> None:
        now = time.monotonic()
        with self._state_lock:
            self._stream_fps.mark(now)

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


def _placeholder_frame(status: ObstacleWebStatus):
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[:, :] = (20, 28, 32)
    text = 'WAITING IMAGE'
    if status.reason != 'waiting':
        text = status.reason.upper()
    cv2.putText(frame, text, (180, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (230, 240, 240), 2, cv2.LINE_AA)
    return frame


def _placeholder_depth_frame(width: int, height: int):
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:, :] = (16, 20, 24)
    cv2.putText(
        frame,
        'WAITING DEPTH',
        (max(8, width // 4), max(32, height // 2)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (210, 220, 220),
        2,
        cv2.LINE_AA,
    )
    return frame


def _depth_to_bgr(depth_image: np.ndarray, *, width: int, height: int):
    depth_m = normalize_depth_to_meters(depth_image)
    valid = np.isfinite(depth_m) & (depth_m > 0.0)
    scaled = np.zeros(depth_m.shape[:2], dtype=np.uint8)
    if np.any(valid):
        clipped = np.clip(depth_m, 0.0, 5.0)
        scaled[valid] = np.uint8(np.clip((1.0 - clipped[valid] / 5.0) * 255.0, 0, 255))
    color = cv2.applyColorMap(scaled, cv2.COLORMAP_TURBO)
    color[~valid] = (0, 0, 0)
    if color.shape[1] != width or color.shape[0] != height:
        color = cv2.resize(color, (width, height), interpolation=cv2.INTER_NEAREST)
        valid_u8 = np.uint8(valid) * 255
        valid_u8 = cv2.resize(valid_u8, (width, height), interpolation=cv2.INTER_NEAREST)
        color[valid_u8 == 0] = (0, 0, 0)
    return color


def _zone_color(is_clear: bool, is_blocked: bool):
    if is_blocked:
        return (35, 35, 235)
    if is_clear:
        return (35, 190, 95)
    return (55, 170, 220)


def _front_zone_bounds(width: int):
    center = width // 2
    front_half = max(1, int(width * FRONT_ZONE_WIDTH_FRACTION / 2.0))
    return max(0, center - front_half), min(width, center + front_half)


def _distance_text(value: Optional[float]) -> str:
    if value is None:
        return '--'
    return '%.2fm' % value


def _draw_label(frame, x: int, y: int, text: str, color) -> None:
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.55
    thickness = 1
    (text_w, text_h), baseline = cv2.getTextSize(text, font, scale, thickness)
    cv2.rectangle(frame, (x, y - text_h - baseline - 8), (x + text_w + 10, y + 5), color, -1)
    cv2.putText(frame, text, (x + 5, y - 4), font, scale, (10, 18, 20), thickness, cv2.LINE_AA)


def main() -> None:
    rclpy.init()
    node = ObstacleWebMonitorNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
