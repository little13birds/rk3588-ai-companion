from __future__ import annotations

import json
import math
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

import rclpy
from geometry_msgs.msg import Quaternion, Twist
from nav_msgs.msg import Odometry
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from sensor_msgs.msg import Imu
from std_msgs.msg import String

from .fused_pose import FusedPoseConfig, FusedPoseEstimator, status_payload
from .web_monitor_utils import FpsCounter


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>融合位姿监控</title>
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
      grid-template-columns: minmax(0, 1fr) 340px;
      gap: 14px;
      padding: 14px;
      min-height: 100vh;
      box-sizing: border-box;
    }
    .plot {
      border: 1px solid #26343b;
      background: #11191d;
      border-radius: 6px;
      min-height: 420px;
      position: relative;
      overflow: hidden;
    }
    canvas { width: 100%; height: 100%; min-height: 420px; display: block; }
    aside {
      border: 1px solid #26343b;
      background: #162027;
      border-radius: 6px;
      padding: 14px;
      box-sizing: border-box;
    }
    h1 { margin: 0 0 12px; font-size: 20px; font-weight: 680; }
    .state {
      display: inline-flex;
      padding: 6px 9px;
      border-radius: 4px;
      margin-bottom: 12px;
      background: #183326;
      color: #79f0a0;
      font-weight: 700;
    }
    .state.warn { background: #34301d; color: #ffd56e; }
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
    button {
      width: 100%;
      margin-top: 14px;
      border: 1px solid #46636d;
      border-radius: 4px;
      background: #20313a;
      color: #edf3f4;
      padding: 9px 10px;
      font: inherit;
      cursor: pointer;
    }
    button:hover { background: #28424c; }
    @media (max-width: 850px) {
      main { grid-template-columns: 1fr; }
      canvas { min-height: 320px; }
    }
  </style>
</head>
<body>
  <main>
    <section class="plot"><canvas id="path"></canvas></section>
    <aside>
      <h1>融合位姿监控</h1>
      <div id="state" class="state warn">等待数据</div>
      <div class="row"><span>前进距离</span><span id="forward">--</span></div>
      <div class="row"><span>横向偏移</span><span id="lateral">--</span></div>
      <div class="row"><span>累计路程</span><span id="distance">--</span></div>
      <div class="row"><span>方向角</span><span id="yaw">--</span></div>
      <div class="row"><span>融合线速度</span><span id="fused_vx">--</span></div>
      <div class="row"><span>融合角速度</span><span id="fused_wz">--</span></div>
      <div class="row"><span>编码器角速度</span><span id="encoder_wz">--</span></div>
      <div class="row"><span>IMU角速度</span><span id="imu_wz">--</span></div>
      <div class="row"><span>陀螺零偏</span><span id="bias">--</span></div>
      <div class="row"><span>位姿发布频率</span><span id="publish_hz">--</span></div>
      <div class="row"><span>速度输入频率</span><span id="velocity_hz">--</span></div>
      <div class="row"><span>IMU输入频率</span><span id="imu_hz">--</span></div>
      <div class="row"><span>ROS_DOMAIN_ID</span><span id="domain">--</span></div>
      <button id="reset" type="button">重置位姿</button>
    </aside>
  </main>
  <script>
    const canvas = document.getElementById('path');
    const ctx = canvas.getContext('2d');

    function meters(value) { return value.toFixed(3) + ' m'; }
    function rps(value) { return value.toFixed(3) + ' rad/s'; }
    function hz(value) { return value.toFixed(2) + ' Hz'; }

    function fitCanvas() {
      const rect = canvas.getBoundingClientRect();
      const scale = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.floor(rect.width * scale));
      canvas.height = Math.max(1, Math.floor(rect.height * scale));
      ctx.setTransform(scale, 0, 0, scale, 0, 0);
      return rect;
    }

    function drawGrid(rect) {
      ctx.fillStyle = '#11191d';
      ctx.fillRect(0, 0, rect.width, rect.height);
      ctx.strokeStyle = '#25343c';
      ctx.lineWidth = 1;
      for (let x = 0; x < rect.width; x += 50) {
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, rect.height); ctx.stroke();
      }
      for (let y = 0; y < rect.height; y += 50) {
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(rect.width, y); ctx.stroke();
      }
      ctx.fillStyle = '#82959c';
      ctx.font = '13px system-ui, sans-serif';
      ctx.fillText('前进方向 ↑', 14, 24);
    }

    function drawPath(data) {
      const rect = fitCanvas();
      drawGrid(rect);
      const history = data.history || [];
      const pose = data.pose;
      const all = history.concat([[pose.lateral_m, pose.forward_m]]);
      const extent = Math.max(1.0, ...all.map(p => Math.max(Math.abs(p[0]), Math.abs(p[1]))));
      const scale = 0.42 * Math.min(rect.width, rect.height) / extent;
      const cx = rect.width / 2;
      const cy = rect.height * 0.78;

      function px(point) { return cx + point[0] * scale; }
      function py(point) { return cy - point[1] * scale; }

      ctx.strokeStyle = '#4aa3ff';
      ctx.lineWidth = 3;
      ctx.beginPath();
      for (let i = 0; i < all.length; i++) {
        const point = all[i];
        if (i === 0) ctx.moveTo(px(point), py(point));
        else ctx.lineTo(px(point), py(point));
      }
      ctx.stroke();

      ctx.fillStyle = '#79f0a0';
      ctx.beginPath();
      ctx.arc(cx, cy, 5, 0, Math.PI * 2);
      ctx.fill();

      const end = [pose.lateral_m, pose.forward_m];
      const yaw = pose.yaw_rad;
      const ex = px(end);
      const ey = py(end);
      const len = 34;
      const dx = Math.sin(yaw) * len;
      const dy = -Math.cos(yaw) * len;
      ctx.strokeStyle = '#ffd56e';
      ctx.lineWidth = 4;
      ctx.beginPath();
      ctx.moveTo(ex, ey);
      ctx.lineTo(ex + dx, ey + dy);
      ctx.stroke();
      ctx.fillStyle = '#ffd56e';
      ctx.beginPath();
      ctx.arc(ex, ey, 7, 0, Math.PI * 2);
      ctx.fill();
    }

    async function refreshStatus() {
      try {
        const res = await fetch('/status.json', { cache: 'no-store' });
        const data = await res.json();
        const state = document.getElementById('state');
        state.textContent = data.state.message;
        state.className = 'state ' + data.state.level;
        document.getElementById('forward').textContent = meters(data.pose.forward_m);
        document.getElementById('lateral').textContent = meters(data.pose.lateral_m);
        document.getElementById('distance').textContent = meters(data.pose.distance_m);
        document.getElementById('yaw').textContent = data.pose.yaw_deg.toFixed(2) + ' deg';
        document.getElementById('fused_vx').textContent = data.velocity.fused_linear_x_mps.toFixed(3) + ' m/s';
        document.getElementById('fused_wz').textContent = rps(data.velocity.fused_angular_z_rps);
        document.getElementById('encoder_wz').textContent = rps(data.velocity.encoder_angular_z_rps);
        document.getElementById('imu_wz').textContent = rps(data.velocity.imu_angular_z_rps);
        document.getElementById('bias').textContent = rps(data.velocity.gyro_bias_z_rps);
        document.getElementById('publish_hz').textContent = hz(data.rates.publish_hz);
        document.getElementById('velocity_hz').textContent = hz(data.rates.velocity_hz);
        document.getElementById('imu_hz').textContent = hz(data.rates.imu_hz);
        document.getElementById('domain').textContent = data.ros.domain_id || '--';
        drawPath(data);
      } catch (err) {
        const state = document.getElementById('state');
        state.textContent = '连接中断';
        state.className = 'state warn';
      }
    }

    document.getElementById('reset').addEventListener('click', async () => {
      await fetch('/reset', { method: 'POST' });
      await refreshStatus();
    });
    window.addEventListener('resize', refreshStatus);
    setInterval(refreshStatus, 120);
    refreshStatus();
  </script>
</body>
</html>
"""


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class FusedPoseMonitorNode(Node):
    def __init__(self):
        super().__init__('fused_pose_monitor')
        self.declare_parameter('velocity_topic', '/vel_raw')
        self.declare_parameter('imu_topic', '/imu/data_raw')
        self.declare_parameter('odom_topic', '/odom_combined')
        self.declare_parameter('status_topic', '/depth_camera/fused_pose_status')
        self.declare_parameter('web_host', '0.0.0.0')
        self.declare_parameter('web_port', 8091)
        self.declare_parameter('publish_period_s', 0.02)
        self.declare_parameter('velocity_timeout_s', 0.30)
        self.declare_parameter('imu_timeout_s', 0.30)
        self.declare_parameter('imu_weight', 0.80)
        self.declare_parameter('imu_yaw_rate_sign', 1.0)
        self.declare_parameter('linear_x_scale', 0.9)
        self.declare_parameter('yaw_rate_scale', 1.53)
        self.declare_parameter('frame_id', 'odom_combined')
        self.declare_parameter('child_frame_id', 'base_link')
        self.declare_parameter('history_limit', 1000)

        config = FusedPoseConfig(
            publish_period_s=float(self.get_parameter('publish_period_s').value),
            velocity_timeout_s=float(self.get_parameter('velocity_timeout_s').value),
            imu_timeout_s=float(self.get_parameter('imu_timeout_s').value),
            imu_weight=float(self.get_parameter('imu_weight').value),
            imu_yaw_rate_sign=float(self.get_parameter('imu_yaw_rate_sign').value),
            linear_x_scale=float(self.get_parameter('linear_x_scale').value),
            yaw_rate_scale=float(self.get_parameter('yaw_rate_scale').value),
        )
        self._lock = threading.RLock()
        self._estimator = FusedPoseEstimator(config)
        self._publish_fps = FpsCounter()
        self._velocity_fps = FpsCounter()
        self._imu_fps = FpsCounter()
        self._history: list[list[float]] = []
        self._server: Optional[ReusableThreadingHTTPServer] = None
        self._server_thread: Optional[threading.Thread] = None

        velocity_topic = str(self.get_parameter('velocity_topic').value)
        imu_topic = str(self.get_parameter('imu_topic').value)
        odom_topic = str(self.get_parameter('odom_topic').value)
        status_topic = str(self.get_parameter('status_topic').value)

        self._odom_pub = self.create_publisher(Odometry, odom_topic, 10)
        self._status_pub = self.create_publisher(String, status_topic, 10)
        self.create_subscription(Twist, velocity_topic, self._on_velocity, 10)
        self.create_subscription(Imu, imu_topic, self._on_imu, 10)
        self.create_timer(config.publish_period_s, self._tick)
        self._start_web_server()
        self.get_logger().info(
            'fused pose monitor velocity=%s imu=%s odom=%s status=%s period=%.3fs'
            % (velocity_topic, imu_topic, odom_topic, status_topic, config.publish_period_s)
        )

    def _now_ros_s(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    def _on_velocity(self, msg: Twist) -> None:
        now_ros = self._now_ros_s()
        now_wall = time.monotonic()
        with self._lock:
            self._estimator.update_velocity(
                linear_x=msg.linear.x,
                angular_z=msg.angular.z,
                stamp_s=now_ros,
            )
            self._velocity_fps.mark(now_wall)

    def _on_imu(self, msg: Imu) -> None:
        now_ros = self._now_ros_s()
        now_wall = time.monotonic()
        with self._lock:
            self._estimator.update_imu(
                angular_z=msg.angular_velocity.z,
                stamp_s=now_ros,
            )
            self._imu_fps.mark(now_wall)

    def _tick(self) -> None:
        now_ros = self._now_ros_s()
        now_wall = time.monotonic()
        with self._lock:
            pose = self._estimator.step(now_ros)
            self._publish_fps.mark(now_wall)
            self._append_history(pose.y, pose.x)
            odom = self._make_odom(now_ros)
            status = String()
            status.data = self._status_json_locked(now_wall)
        self._odom_pub.publish(odom)
        self._status_pub.publish(status)

    def _append_history(self, lateral_m: float, forward_m: float) -> None:
        self._history.append([round(lateral_m, 4), round(forward_m, 4)])
        limit = int(self.get_parameter('history_limit').value)
        if len(self._history) > limit:
            del self._history[: len(self._history) - limit]

    def _make_odom(self, stamp_s: float) -> Odometry:
        frame_id = str(self.get_parameter('frame_id').value)
        child_frame_id = str(self.get_parameter('child_frame_id').value)
        pose = self._estimator.pose
        msg = Odometry()
        stamp_sec = int(stamp_s)
        stamp_nanosec = int((stamp_s - stamp_sec) * 1e9)
        msg.header.stamp.sec = stamp_sec
        msg.header.stamp.nanosec = stamp_nanosec
        msg.header.frame_id = frame_id
        msg.child_frame_id = child_frame_id
        msg.pose.pose.position.x = pose.x
        msg.pose.pose.position.y = pose.y
        msg.pose.pose.position.z = 0.0
        msg.pose.pose.orientation = _yaw_to_quaternion(pose.yaw)
        msg.twist.twist.linear.x = self._estimator.last_fused_linear_x
        msg.twist.twist.angular.z = self._estimator.last_fused_angular_z
        msg.pose.covariance = [
            0.02, 0.0, 0.0, 0.0, 0.0, 0.0,
            0.0, 0.05, 0.0, 0.0, 0.0, 0.0,
            0.0, 0.0, 999.0, 0.0, 0.0, 0.0,
            0.0, 0.0, 0.0, 999.0, 0.0, 0.0,
            0.0, 0.0, 0.0, 0.0, 999.0, 0.0,
            0.0, 0.0, 0.0, 0.0, 0.0, 0.05,
        ]
        return msg

    def _status_json_locked(self, now_wall_s: float) -> str:
        payload = status_payload(
            self._estimator,
            publish_hz=self._publish_fps.fps(now_wall_s),
            velocity_hz=self._velocity_fps.fps(now_wall_s),
            imu_hz=self._imu_fps.fps(now_wall_s),
            source_domain_id=os.environ.get('ROS_DOMAIN_ID', ''),
        )
        payload['history'] = list(self._history)
        payload['topics'] = {
            'velocity': str(self.get_parameter('velocity_topic').value),
            'imu': str(self.get_parameter('imu_topic').value),
            'odom': str(self.get_parameter('odom_topic').value),
            'status': str(self.get_parameter('status_topic').value),
        }
        payload['age'] = {
            'velocity_s': _age(self._estimator.last_step_s, self._estimator.velocity_stamp_s),
            'imu_s': _age(self._estimator.last_step_s, self._estimator.imu_stamp_s),
        }
        payload['state'] = _state_message(payload)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def get_status_json(self) -> str:
        with self._lock:
            return self._status_json_locked(time.monotonic())

    def reset_pose(self) -> None:
        with self._lock:
            self._estimator.reset(self._now_ros_s())
            self._history.clear()

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
                else:
                    self.send_error(404, 'not found')

            def do_POST(self):  # noqa: N802
                if self.path.startswith('/reset'):
                    node.reset_pose()
                    self._send_json('{"ok": true}')
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

            def log_message(self, format, *args):  # noqa: A002,N802
                return

        self._server = ReusableThreadingHTTPServer((host, port), Handler)
        self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._server_thread.start()
        self.get_logger().info('fused pose monitor available at http://%s:%d' % (host, port))

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


def _yaw_to_quaternion(yaw: float) -> Quaternion:
    half = yaw / 2.0
    quat = Quaternion()
    quat.x = 0.0
    quat.y = 0.0
    quat.z = math.sin(half)
    quat.w = math.cos(half)
    return quat


def _age(now_s: Optional[float], stamp_s: Optional[float]) -> Optional[float]:
    if now_s is None or stamp_s is None:
        return None
    return round(max(0.0, now_s - stamp_s), 3)


def _state_message(payload: dict) -> dict:
    velocity_fresh = bool(payload['fresh']['velocity'])
    imu_fresh = bool(payload['fresh']['imu'])
    if velocity_fresh and imu_fresh:
        return {'level': '', 'message': '融合位姿运行中'}
    if velocity_fresh:
        return {'level': 'warn', 'message': '等待IMU数据'}
    if imu_fresh:
        return {'level': 'warn', 'message': '等待速度数据'}
    return {'level': 'warn', 'message': '等待速度和IMU数据'}


def main() -> None:
    rclpy.init()
    node = FusedPoseMonitorNode()
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
