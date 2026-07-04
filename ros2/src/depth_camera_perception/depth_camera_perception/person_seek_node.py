from __future__ import annotations

import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterable, Optional

import cv2
import rclpy
from cv_bridge import CvBridge
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import CameraInfo, Image
from std_msgs.msg import String

from .depth_utils import estimate_bbox_depth_m, nearest_detection
from .person_detector import Detection, create_person_detector
from .person_seek import PersonSeekConfig, PersonSeekController, PersonTarget, SeekOutput


OBSTACLE_BYPASS_RELEASE_REASONS = {
    "avoid_forward_return_heading",
    "avoid_forward_exit_hold",
}


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>深度找人监控</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #070c10;
      --panel: #121b22;
      --line: #263641;
      --text: #eef5f7;
      --muted: #97a9b3;
      --green: #78e08f;
      --yellow: #ffd36a;
      --red: #ff7675;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      gap: 16px;
      padding: 16px;
      min-height: 100vh;
    }
    .view, .side {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      overflow: hidden;
    }
    .view {
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 420px;
    }
    img {
      display: block;
      width: 100%;
      height: 100%;
      object-fit: contain;
      background: #020405;
    }
    .side {
      padding: 20px;
    }
    h1 {
      margin: 0 0 20px;
      font-size: 28px;
      line-height: 1.2;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 38px;
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 22px;
      font-weight: 800;
      background: rgba(120, 224, 143, 0.14);
      color: var(--green);
    }
    .badge.warn {
      background: rgba(255, 211, 106, 0.16);
      color: var(--yellow);
    }
    .badge.danger {
      background: rgba(255, 118, 117, 0.16);
      color: var(--red);
    }
    dl { margin: 22px 0 0; }
    .row {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 0;
      border-bottom: 1px solid var(--line);
      font-size: 18px;
    }
    dt { color: var(--muted); }
    dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; }
      .side { order: -1; }
    }
  </style>
</head>
<body>
  <main>
    <section class="view"><img src="/stream.mjpg" alt="找人推理画面"></section>
    <aside class="side">
      <h1>深度找人监控</h1>
      <div id="state" class="badge warn">等待状态</div>
      <dl>
        <div class="row"><dt>状态原因</dt><dd id="reason">--</dd></div>
        <div class="row"><dt>目标人物</dt><dd id="identity_target">--</dd></div>
        <div class="row"><dt>绑定 ID</dt><dd id="identity_track">--</dd></div>
        <div class="row"><dt>身份状态</dt><dd id="identity_state">--</dd></div>
        <div class="row"><dt>身份分数</dt><dd id="identity_score">--</dd></div>
        <div class="row"><dt>检测人数</dt><dd id="target_count">--</dd></div>
        <div class="row"><dt>目标距离</dt><dd id="distance">--</dd></div>
        <div class="row"><dt>目标置信度</dt><dd id="confidence">--</dd></div>
        <div class="row"><dt>中心偏差</dt><dd id="center_error">--</dd></div>
        <div class="row"><dt>输出线速度</dt><dd id="linear_x">--</dd></div>
        <div class="row"><dt>输出角速度</dt><dd id="angular_z">--</dd></div>
        <div class="row"><dt>扫描角度</dt><dd id="scan_yaw">--</dd></div>
        <div class="row"><dt>扫描时间</dt><dd id="scan_elapsed">--</dd></div>
        <div class="row"><dt>画面延迟</dt><dd id="frame_age">--</dd></div>
        <div class="row"><dt>网页 FPS</dt><dd id="web_fps">--</dd></div>
        <div class="row"><dt>分辨率</dt><dd id="resolution">--</dd></div>
      </dl>
    </aside>
  </main>
  <script>
    const stateText = {
      IDLE: "未启动",
      SEARCH_ROTATE: "旋转找人",
      APPROACH: "接近目标",
      OBSTACLE_TAKEOVER: "避障接管",
      ARRIVED: "已到达",
      SEARCH_FAILED: "未找到人",
      TARGET_LOST: "目标丢失"
    };
    const fmt = (value, suffix = "", digits = 2) =>
      value === null || value === undefined ? "--" : `${Number(value).toFixed(digits)}${suffix}`;
    async function refresh() {
      try {
        const data = await fetch("/status.json", { cache: "no-store" }).then(r => r.json());
        const state = document.getElementById("state");
        state.textContent = stateText[data.state] || data.state || "--";
        state.className = "badge";
        if (["SEARCH_ROTATE", "SEARCH_FAILED", "TARGET_LOST", "OBSTACLE_TAKEOVER"].includes(data.state)) state.classList.add("warn");
        if (data.reason === "waiting_camera") state.classList.add("danger");
        document.getElementById("reason").textContent = data.reason || "--";
        const identity = data.identity || {};
        document.getElementById("identity_target").textContent = identity.target_name || "--";
        document.getElementById("identity_track").textContent = identity.target_track_id ?? "--";
        document.getElementById("identity_state").textContent = identity.identity_state || identity.mode || "--";
        document.getElementById("identity_score").textContent = fmt(identity.identity_score, "", 3);
        document.getElementById("target_count").textContent = data.target_count ?? "--";
        document.getElementById("distance").textContent = fmt(data.target_distance_m, " m");
        document.getElementById("confidence").textContent = fmt(data.target_confidence, "", 2);
        document.getElementById("center_error").textContent = fmt(data.target_center_error, "", 3);
        document.getElementById("linear_x").textContent = fmt(data.output_linear_x, " m/s", 3);
        document.getElementById("angular_z").textContent = fmt(data.output_angular_z, " rad/s", 3);
        document.getElementById("scan_yaw").textContent = fmt(data.scan_yaw_rad, " rad", 2);
        document.getElementById("scan_elapsed").textContent = fmt(data.scan_elapsed_s, " s", 2);
        document.getElementById("frame_age").textContent = fmt(data.latest_frame_age_s, " s", 2);
        document.getElementById("web_fps").textContent = fmt(data.fps?.web, "", 2);
        document.getElementById("resolution").textContent =
          data.image?.width && data.image?.height ? `${data.image.width}x${data.image.height}` : "--";
      } catch (err) {
        document.getElementById("state").textContent = "连接中断";
        document.getElementById("state").className = "badge danger";
      }
    }
    setInterval(refresh, 250);
    refresh();
  </script>
</body>
</html>
"""


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class _FpsCounter:
    def __init__(self, window_s: float = 2.0):
        self._window_s = float(window_s)
        self._times: list[float] = []
        self._lock = threading.Lock()

    def tick(self, now_s: Optional[float] = None) -> None:
        now = time.monotonic() if now_s is None else float(now_s)
        with self._lock:
            self._times.append(now)
            self._times = [t for t in self._times if now - t <= self._window_s]

    @property
    def rate(self) -> float:
        now = time.monotonic()
        with self._lock:
            self._times = [t for t in self._times if now - t <= self._window_s]
            if len(self._times) <= 1:
                return 0.0
            span = max(self._times[-1] - self._times[0], 1e-6)
            return (len(self._times) - 1) / span


class PersonSeekNode(Node):
    def __init__(self):
        super().__init__("person_seek")
        self.declare_parameter("color_topic", "/camera/color/image_raw")
        self.declare_parameter("depth_topic", "/camera/depth/image_raw")
        self.declare_parameter("camera_info_topic", "/camera/color/camera_info")
        self.declare_parameter("odom_topic", "/odom_combined")
        self.declare_parameter("cmd_vel_raw_topic", "/cmd_vel_raw")
        self.declare_parameter("status_topic", "/depth_camera/person_seek_status")
        self.declare_parameter("obstacle_status_topic", "/depth_camera/obstacle_status")
        self.declare_parameter("detector_backend", "rknn")
        self.declare_parameter("model_path", "/home/elf/ros2/yolov8n-board-rk3588-fp.rknn")
        self.declare_parameter("mode", "nearest")
        self.declare_parameter("target_name", "")
        self.declare_parameter("face_identity_root", "/home/elf/face_identity_rk3588")
        self.declare_parameter(
            "pose_model_path",
            "/home/elf/face_identity_rk3588/models/rknn/pose_yolov8n_hybrid.rknn",
        )
        self.declare_parameter(
            "pose_lib_path",
            "/home/elf/face_identity_rk3588/native/build/libperson_pose.so",
        )
        self.declare_parameter("identity_lost_timeout_s", 0.50)
        self.declare_parameter("confidence", 0.4)
        self.declare_parameter("roi_fraction", 0.5)
        self.declare_parameter("process_period_sec", 0.10)
        self.declare_parameter("control_period_sec", 0.10)
        self.declare_parameter("camera_timeout_s", 0.50)
        self.declare_parameter("auto_start", True)
        self.declare_parameter("stop_distance_m", 0.8)
        self.declare_parameter("stop_tolerance_m", 0.05)
        self.declare_parameter("search_angular_z", 0.25)
        self.declare_parameter("search_max_yaw_rad", 2.0 * math.pi)
        self.declare_parameter("search_timeout_s", 30.0)
        self.declare_parameter("approach_max_forward_mps", 0.40)
        self.declare_parameter("approach_slow_forward_mps", 0.10)
        self.declare_parameter("slowdown_distance_m", 1.20)
        self.declare_parameter("approach_angular_gain", 0.8)
        self.declare_parameter("approach_max_angular_z", 0.25)
        self.declare_parameter("center_tolerance_fraction", 0.08)
        self.declare_parameter("target_lost_timeout_s", 0.50)
        self.declare_parameter("obstacle_status_timeout_s", 0.50)
        self.declare_parameter("web_host", "0.0.0.0")
        self.declare_parameter("web_port", 8092)
        self.declare_parameter("jpeg_quality", 80)
        self.declare_parameter("stream_period_sec", 0.20)

        self._bridge = CvBridge()
        self._latest_color: Optional[Image] = None
        self._latest_depth: Optional[Image] = None
        self._latest_color_s: Optional[float] = None
        self._latest_depth_s: Optional[float] = None
        self._latest_yaw_rad: Optional[float] = None
        self._latest_obstacle_status: Optional[dict] = None
        self._latest_obstacle_status_s: Optional[float] = None
        self._camera_width: Optional[int] = None
        self._camera_height: Optional[int] = None
        self._last_process_s = 0.0
        self._last_publish_s = 0.0
        self._last_output = SeekOutput(state="IDLE", reason="idle")
        self._last_target_count = 0
        self._terminal_zero_sent = False
        self._web_lock = threading.Lock()
        self._latest_jpeg: Optional[bytes] = None
        self._latest_jpeg_s: Optional[float] = None
        self._latest_image_width = 0
        self._latest_image_height = 0
        self._web_fps = _FpsCounter()
        self._server: Optional[ReusableThreadingHTTPServer] = None
        self._server_thread: Optional[threading.Thread] = None

        backend = str(self.get_parameter("detector_backend").value)
        model_path = str(self.get_parameter("model_path").value)
        confidence = float(self.get_parameter("confidence").value)
        mode = str(self.get_parameter("mode").value)
        face_identity_root = str(self.get_parameter("face_identity_root").value)
        if mode == "identity":
            backend = "pose_rknn"
            model_path = str(self.get_parameter("pose_model_path").value)
        self._detector = create_person_detector(
            backend,
            model_path,
            confidence,
            face_identity_root=face_identity_root,
            pose_lib_path=str(self.get_parameter("pose_lib_path").value),
        )
        self._controller = PersonSeekController(self._config())
        self._identity_adapter = None
        self._identity_tracker = None
        self._last_identity_status = {"mode": mode}
        if mode == "identity":
            from .face_identity_adapter import FaceIdentityAdapter
            from .identity_tracking import IdentityTargetTracker

            self._identity_adapter = FaceIdentityAdapter(face_identity_root=face_identity_root)
            target_name = str(self.get_parameter("target_name").value).strip()
            target_identity = self._identity_adapter.load_target_identity(target_name)
            self._identity_tracker = IdentityTargetTracker(
                target_identity,
                identity_lost_timeout_s=float(
                    self.get_parameter("identity_lost_timeout_s").value
                ),
            )
            self._last_identity_status = {
                "mode": "identity",
                "target_name": target_name,
                "target_person_id": target_identity.person_id,
                "target_display_name": target_identity.display_name,
                "target_track_id": None,
                "identity_state": "SEARCH_IDENTITY",
                "identity_reason": "target_identity_not_visible",
                "identity_score": None,
                "temporary_lost_due_to_obstacle": False,
            }

        color_topic = str(self.get_parameter("color_topic").value)
        depth_topic = str(self.get_parameter("depth_topic").value)
        camera_info_topic = str(self.get_parameter("camera_info_topic").value)
        odom_topic = str(self.get_parameter("odom_topic").value)
        cmd_topic = str(self.get_parameter("cmd_vel_raw_topic").value)
        status_topic = str(self.get_parameter("status_topic").value)
        obstacle_status_topic = str(self.get_parameter("obstacle_status_topic").value)

        self.create_subscription(Image, color_topic, self._on_color, 10)
        self.create_subscription(Image, depth_topic, self._on_depth, 10)
        self.create_subscription(CameraInfo, camera_info_topic, self._on_camera_info, 10)
        self.create_subscription(Odometry, odom_topic, self._on_odom, 10)
        self.create_subscription(String, obstacle_status_topic, self._on_obstacle_status, 10)
        self._cmd_pub = self.create_publisher(Twist, cmd_topic, 10)
        self._status_pub = self.create_publisher(String, status_topic, 10)
        self.create_timer(0.05, self._tick)
        self._start_web_server()

        if bool(self.get_parameter("auto_start").value):
            self._controller.start(now_s=time.monotonic(), yaw_rad=self._latest_yaw_rad)

        self.get_logger().info(
            "person seek listening color=%s depth=%s odom=%s obstacle=%s output=%s status=%s"
            % (color_topic, depth_topic, odom_topic, obstacle_status_topic, cmd_topic, status_topic)
        )

    def destroy_node(self) -> bool:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        return super().destroy_node()

    def _config(self) -> PersonSeekConfig:
        return PersonSeekConfig(
            stop_distance_m=float(self.get_parameter("stop_distance_m").value),
            stop_tolerance_m=float(self.get_parameter("stop_tolerance_m").value),
            search_angular_z=float(self.get_parameter("search_angular_z").value),
            search_max_yaw_rad=float(self.get_parameter("search_max_yaw_rad").value),
            search_timeout_s=float(self.get_parameter("search_timeout_s").value),
            approach_max_forward_mps=float(self.get_parameter("approach_max_forward_mps").value),
            approach_slow_forward_mps=float(
                self.get_parameter("approach_slow_forward_mps").value
            ),
            slowdown_distance_m=float(self.get_parameter("slowdown_distance_m").value),
            approach_angular_gain=float(self.get_parameter("approach_angular_gain").value),
            approach_max_angular_z=float(self.get_parameter("approach_max_angular_z").value),
            center_tolerance_fraction=float(self.get_parameter("center_tolerance_fraction").value),
            target_lost_timeout_s=float(self.get_parameter("target_lost_timeout_s").value),
        )

    def _on_color(self, msg: Image) -> None:
        self._latest_color = msg
        self._latest_color_s = time.monotonic()

    def _on_depth(self, msg: Image) -> None:
        self._latest_depth = msg
        self._latest_depth_s = time.monotonic()

    def _on_camera_info(self, msg: CameraInfo) -> None:
        self._camera_width = int(msg.width)
        self._camera_height = int(msg.height)

    def _on_odom(self, msg: Odometry) -> None:
        self._latest_yaw_rad = _yaw_from_quaternion(msg.pose.pose.orientation)

    def _on_obstacle_status(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if isinstance(payload, dict):
            self._latest_obstacle_status = payload
            self._latest_obstacle_status_s = time.monotonic()

    def _tick(self) -> None:
        now = time.monotonic()
        self._controller.config = self._config()

        if self._camera_stale(now):
            if self._obstacle_bypass_active(now):
                self._last_output = _obstacle_takeover_status(self._last_output)
            elif self._controller.state not in {"IDLE", "ARRIVED", "SEARCH_FAILED", "TARGET_LOST"}:
                self._last_output = SeekOutput(state=self._controller.state, reason="waiting_camera")
                if _person_seek_should_publish_zero(self._last_output):
                    self._publish_zero()
            self._publish_status()
            return

        if now - self._last_process_s >= float(self.get_parameter("process_period_sec").value):
            self._last_process_s = now
            target = self._detect_target()
            if self._obstacle_bypass_active(now, target_available=target is not None):
                self._last_output = _obstacle_takeover_status(self._last_output)
            else:
                self._last_output = self._controller.update(
                    target=target,
                    now_s=now,
                    yaw_rad=self._latest_yaw_rad,
                )
            if self._last_output.state in {"ARRIVED", "SEARCH_FAILED", "TARGET_LOST"}:
                self._terminal_zero_sent = False

        if now - self._last_publish_s >= float(self.get_parameter("control_period_sec").value):
            self._last_publish_s = now
            if _person_seek_should_publish(self._last_output):
                self._publish_output(self._last_output)
            elif not self._terminal_zero_sent and _person_seek_should_publish_zero(self._last_output):
                self._publish_zero()
                self._terminal_zero_sent = True
            self._publish_status()

    def _camera_stale(self, now: float) -> bool:
        timeout = float(self.get_parameter("camera_timeout_s").value)
        if self._latest_color is None or self._latest_depth is None:
            return True
        if self._latest_color_s is None or self._latest_depth_s is None:
            return True
        return now - self._latest_color_s > timeout or now - self._latest_depth_s > timeout

    def _obstacle_bypass_active(self, now: float, *, target_available: bool = False) -> bool:
        if self._latest_obstacle_status_s is None:
            return False
        timeout = float(self.get_parameter("obstacle_status_timeout_s").value)
        if now - self._latest_obstacle_status_s > timeout:
            return False
        return _obstacle_bypass_active(
            self._latest_obstacle_status,
            target_available=target_available,
        )

    def _detect_target(self) -> Optional[PersonTarget]:
        if self._latest_color is None or self._latest_depth is None:
            self._last_target_count = 0
            return None
        color = self._bridge.imgmsg_to_cv2(self._latest_color, desired_encoding="bgr8")
        depth = self._bridge.imgmsg_to_cv2(self._latest_depth, desired_encoding="passthrough")
        image_height, image_width = color.shape[:2]
        roi_fraction = float(self.get_parameter("roi_fraction").value)

        detections = []
        for detection in self._detector.detect(color):
            distance_m = estimate_bbox_depth_m(depth, detection.bbox, fraction=roi_fraction)
            detections.append(detection.with_distance(distance_m))
        self._last_target_count = len(detections)
        if str(self.get_parameter("mode").value) == "identity":
            target = self._select_identity_target(color, detections, image_width, image_height)
        else:
            target = _select_target(detections, image_width=image_width, image_height=image_height)
        self._update_latest_jpeg(color, detections, target)
        return target

    def _select_identity_target(
        self,
        color_bgr,
        detections: list[Detection],
        image_width: int,
        image_height: int,
    ) -> Optional[PersonTarget]:
        from .identity_tracking import associate_faces_to_people, detections_to_pose_people

        if self._identity_adapter is None or self._identity_tracker is None:
            return None
        now = time.monotonic()
        faces = self._identity_adapter.infer_faces_bgr(color_bgr)
        people = detections_to_pose_people(detections)
        observations = associate_faces_to_people(people, faces)
        selection = self._identity_tracker.update(
            observations,
            now_s=now,
            obstacle_active=self._obstacle_bypass_active(now, target_available=False),
        )
        self._last_identity_status = {
            "mode": "identity",
            "target_name": str(self.get_parameter("target_name").value),
            "target_person_id": selection.target_person_id,
            "target_display_name": selection.target_display_name,
            "target_track_id": selection.bound_track_id,
            "identity_state": selection.state,
            "identity_reason": selection.reason,
            "identity_score": _rounded_or_none(selection.identity_score),
            "temporary_lost_due_to_obstacle": selection.temporary_lost_due_to_obstacle,
        }
        if selection.target is None or selection.target.distance_m is None:
            return None
        return PersonTarget(
            bbox=selection.target.person.bbox,
            confidence=selection.target.person.confidence,
            distance_m=float(selection.target.distance_m),
            image_width=image_width,
            image_height=image_height,
        )

    def _update_latest_jpeg(
        self,
        color_bgr,
        detections: list[Detection],
        selected: Optional[PersonTarget],
    ) -> None:
        annotated = _draw_seek_overlay(color_bgr, detections, selected, self._last_output)
        quality = int(self.get_parameter("jpeg_quality").value)
        ok, encoded = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            return
        image_height, image_width = annotated.shape[:2]
        with self._web_lock:
            self._latest_jpeg = encoded.tobytes()
            self._latest_jpeg_s = time.monotonic()
            self._latest_image_width = int(image_width)
            self._latest_image_height = int(image_height)

    def _publish_output(self, output: SeekOutput) -> None:
        msg = Twist()
        msg.linear.x = float(output.linear_x)
        msg.angular.z = float(output.angular_z)
        self._cmd_pub.publish(msg)

    def _publish_zero(self) -> None:
        self._cmd_pub.publish(Twist())

    def _publish_status(self) -> None:
        msg = String()
        msg.data = self._current_status_json()
        self._status_pub.publish(msg)

    def _current_status_json(self) -> str:
        return json.dumps(self._current_status_payload(), ensure_ascii=False, sort_keys=True)

    def _current_status_payload(self) -> dict:
        now = time.monotonic()
        with self._web_lock:
            latest_frame_age_s = (
                None if self._latest_jpeg_s is None else max(0.0, now - self._latest_jpeg_s)
            )
            image_width = self._latest_image_width
            image_height = self._latest_image_height
        return _seek_status_payload(
            self._last_output,
            target_count=self._last_target_count,
            image_width=image_width,
            image_height=image_height,
            web_fps=self._web_fps.rate,
            latest_frame_age_s=latest_frame_age_s,
            identity_status=self._last_identity_status,
        )

    def _snapshot_jpeg(self) -> Optional[bytes]:
        with self._web_lock:
            return self._latest_jpeg

    def _start_web_server(self) -> None:
        host = str(self.get_parameter("web_host").value)
        port = int(self.get_parameter("web_port").value)
        stream_period = float(self.get_parameter("stream_period_sec").value)
        node = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path in {"/", "/index.html"}:
                    self._send_bytes(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
                    return
                if self.path == "/status.json":
                    self._send_bytes(
                        node._current_status_json().encode("utf-8"),
                        "application/json; charset=utf-8",
                    )
                    return
                if self.path == "/snapshot.jpg":
                    frame = node._snapshot_jpeg()
                    if frame is None:
                        self._send_bytes(b"no frame", "text/plain; charset=utf-8", status=503)
                        return
                    self._send_bytes(frame, "image/jpeg")
                    return
                if self.path == "/stream.mjpg":
                    self._send_stream()
                    return
                self.send_error(404)

            def _send_stream(self):
                self.send_response(200)
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                self.send_header("Pragma", "no-cache")
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                while True:
                    frame = node._snapshot_jpeg()
                    if frame is not None:
                        try:
                            self.wfile.write(b"--frame\r\n")
                            self.wfile.write(b"Content-Type: image/jpeg\r\n")
                            self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
                            self.wfile.write(frame)
                            self.wfile.write(b"\r\n")
                            node._web_fps.tick()
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            return
                    time.sleep(max(0.02, stream_period))

            def _send_bytes(self, body: bytes, content_type: str, status: int = 200):
                self.send_response(status)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        self._server = ReusableThreadingHTTPServer((host, port), Handler)
        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            name="person_seek_web",
            daemon=True,
        )
        self._server_thread.start()
        self.get_logger().info("person seek web listening on http://%s:%d" % (host, port))


def _select_target(
    detections: Iterable[Detection],
    *,
    image_width: int,
    image_height: int,
) -> Optional[PersonTarget]:
    nearest = nearest_detection(detections)
    if nearest is None:
        return None
    return _detection_to_target(nearest, image_width=image_width, image_height=image_height)


def _detection_to_target(
    detection: Detection,
    *,
    image_width: int,
    image_height: int,
) -> Optional[PersonTarget]:
    if detection.distance_m is None:
        return None
    if not math.isfinite(detection.distance_m) or detection.distance_m <= 0.0:
        return None
    return PersonTarget(
        bbox=detection.bbox,
        confidence=float(detection.confidence),
        distance_m=float(detection.distance_m),
        image_width=int(image_width),
        image_height=int(image_height),
    )


def _build_seek_status_json(output: SeekOutput, target_count: int) -> str:
    return json.dumps(
        _seek_status_payload(output, target_count=target_count),
        ensure_ascii=False,
        sort_keys=True,
    )


def _obstacle_bypass_active(status: Optional[dict], *, target_available: bool = False) -> bool:
    if not isinstance(status, dict):
        return False
    reason = str(status.get("reason", ""))
    if reason in OBSTACLE_BYPASS_RELEASE_REASONS:
        return not target_available
    phase = status.get("avoidance_phase")
    if phase is None:
        return False
    phase = str(phase)
    return phase not in {"", "cruise", "idle", "none", "None"}


def _obstacle_takeover_status(previous: SeekOutput) -> SeekOutput:
    return SeekOutput(
        state="OBSTACLE_TAKEOVER",
        reason="obstacle_guard_takeover",
        linear_x=0.0,
        angular_z=0.0,
        scan_yaw_rad=previous.scan_yaw_rad,
        scan_elapsed_s=previous.scan_elapsed_s,
    )


def _person_seek_should_publish(output: SeekOutput) -> bool:
    return output.state in {"SEARCH_ROTATE", "APPROACH"} and output.reason != "obstacle_guard_takeover"


def _person_seek_should_publish_zero(output: SeekOutput) -> bool:
    if output.state in {"IDLE", "OBSTACLE_TAKEOVER"}:
        return False
    if output.reason == "obstacle_guard_takeover":
        return False
    return not _person_seek_should_publish(output)


def _seek_status_payload(
    output: SeekOutput,
    *,
    target_count: int,
    image_width: int = 0,
    image_height: int = 0,
    web_fps: float = 0.0,
    latest_frame_age_s: Optional[float] = None,
    identity_status: Optional[dict] = None,
) -> dict:
    payload = {
        "state": output.state,
        "reason": output.reason,
        "target_count": int(target_count),
        "target_distance_m": _rounded_or_none(output.target_distance_m),
        "target_confidence": _rounded_or_none(output.target_confidence),
        "target_center_error": _rounded_or_none(output.target_center_error),
        "output_linear_x": round(float(output.linear_x), 3),
        "output_angular_z": round(float(output.angular_z), 3),
        "scan_yaw_rad": round(float(output.scan_yaw_rad), 3),
        "scan_elapsed_s": round(float(output.scan_elapsed_s), 3),
        "latest_frame_age_s": _rounded_or_none(latest_frame_age_s),
        "target": {
            "distance_m": _rounded_or_none(output.target_distance_m),
            "confidence": _rounded_or_none(output.target_confidence),
            "center_error": _rounded_or_none(output.target_center_error),
        },
        "motion": {
            "linear_x": round(float(output.linear_x), 3),
            "angular_z": round(float(output.angular_z), 3),
        },
        "scan": {
            "yaw_rad": round(float(output.scan_yaw_rad), 3),
            "elapsed_s": round(float(output.scan_elapsed_s), 3),
        },
        "image": {
            "width": int(image_width),
            "height": int(image_height),
        },
        "fps": {
            "web": round(float(web_fps), 2),
        },
        "identity": identity_status or {"mode": "nearest"},
    }
    return payload


def _draw_seek_overlay(
    frame_bgr,
    detections: Iterable[Detection],
    selected: Optional[PersonTarget],
    output: SeekOutput,
):
    annotated = frame_bgr.copy()
    selected_bbox = tuple(selected.bbox) if selected is not None else None

    for detection in detections:
        x1, y1, x2, y2 = [int(round(v)) for v in detection.bbox]
        is_selected = selected_bbox == tuple(detection.bbox)
        color = (0, 220, 255) if is_selected else (80, 220, 120)
        thickness = 3 if is_selected else 2
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, thickness)
        distance = "--" if detection.distance_m is None else f"{detection.distance_m:.2f}m"
        track = "" if detection.track_id is None else f" id:{detection.track_id}"
        label = f"{detection.label}{track} {detection.confidence:.2f} {distance}"
        if is_selected:
            label = "TARGET " + label
        label_y = max(18, y1 - 8)
        cv2.putText(
            annotated,
            label,
            (x1, label_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
            cv2.LINE_AA,
        )

    status_color = (80, 220, 120)
    if output.state in {"SEARCH_ROTATE", "SEARCH_FAILED", "TARGET_LOST"}:
        status_color = (0, 210, 255)
    if output.reason == "waiting_camera":
        status_color = (80, 80, 255)
    cv2.rectangle(annotated, (0, 0), (annotated.shape[1], 42), status_color, -1)
    status = (
        f"{output.state} | {output.reason} | "
        f"vx {output.linear_x:.2f} m/s wz {output.angular_z:.2f} rad/s"
    )
    cv2.putText(
        annotated,
        status,
        (12, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return annotated


def _rounded_or_none(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), 3)


def _yaw_from_quaternion(q) -> float:
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


def main() -> None:
    rclpy.init()
    node = PersonSeekNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
