#!/usr/bin/env python3
"""
arm_agent: 摄像头唯一拥有者 + 书本 NPU 检测 + 舵机静止判定 + HTTP 接口。
扩展自 book_servo_bridge，供 cloud-model 读书模式联动。

  - 60Hz 采帧 → NPU 检测 → 最新帧缓冲 + 归一化 /face_info（tracking 时）
  - 订阅 /joint_states → MotionSettleTracker 判定舵机静止
  - tracking 开/关 由 HTTP /reading/start|stop 控制
  - HTTP :8642  GET /frame.jpg[?wait_ready=1&timeout=N]  GET /book/status
                POST /reading/prepare  POST /reading/start  POST /reading/stop

用法: ros2 run face_track arm_agent
"""
import ctypes
import json
import math
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import cv2
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, Float32MultiArray, Int8
from sensor_msgs.msg import JointState

from face_track.arm_agent_core import (
    InferenceStats,
    MotionSettleTracker,
    PrepareCommandRepublisher,
    build_book_debug,
    build_joint_debug,
    reading_ready,
    scaled_debug_size,
    select_camera_source,
)

LIB_PATH = os.path.expanduser("~/book_detect/build/libbook_detect.so")
MODEL_PATH = os.path.expanduser("~/book_detect/model/best_hybrid_v9.rknn")

_lib = ctypes.CDLL(LIB_PATH)
_lib.book_detect_init.argtypes = [ctypes.c_char_p]
_lib.book_detect_init.restype = ctypes.c_void_p
_lib.book_detect_release.argtypes = [ctypes.c_void_p]
_lib.book_detect_release.restype = None
_lib.book_detect_infer.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int]
_lib.book_detect_infer.restype = ctypes.c_void_p

_libc = ctypes.CDLL(None)
_libc.free.argtypes = [ctypes.c_void_p]
_libc.free.restype = None


def _wait_timeout(query):
    raw = query.get("timeout", ["25"])[0]
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 25.0
    if not math.isfinite(value):
        return 25.0
    return max(0.0, min(value, 35.0))


def _debug_page_html():
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Reading Arm Debug</title>
  <style>
    :root { color-scheme: dark; --bg:#0b0e11; --panel:#151a20; --line:#2a333d; --text:#e8edf2; --muted:#8c99a6; --ok:#39d98a; --warn:#f5c542; --bad:#ff6b6b; --accent:#4cc9f0; }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--text); font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; letter-spacing: 0; }
    main { height: 100vh; display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: 12px; padding: 12px; }
    .stage { min-width: 0; display: grid; grid-template-rows: auto minmax(0, 1fr); gap: 8px; }
    .topbar { display:flex; align-items:center; justify-content:space-between; gap: 12px; min-height: 32px; }
    h1 { font-size: 18px; line-height: 1.2; margin: 0; font-weight: 650; }
    .hint { color: var(--muted); font-size: 13px; white-space: nowrap; }
    .viewer { position: relative; min-height: 0; border: 1px solid var(--line); background: #050607; overflow: hidden; }
    canvas { display: block; width: 100%; height: 100%; object-fit: contain; }
    .side { min-width: 0; display: grid; grid-template-rows: auto auto auto auto minmax(0, 1fr); gap: 10px; overflow: auto; }
    .panel { border: 1px solid var(--line); background: var(--panel); padding: 12px; }
    .panel h2 { font-size: 14px; margin: 0 0 10px; color: var(--muted); font-weight: 650; }
    .controls { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    button { min-height: 40px; border: 1px solid var(--line); border-radius: 6px; background: #202832; color: var(--text); font: inherit; font-size: 13px; cursor: pointer; }
    button:hover { border-color: var(--accent); }
    button:disabled { color: var(--muted); cursor: wait; opacity: .75; }
    .control-status { min-height: 20px; margin-top: 8px; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
    .state-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .kv { min-width: 0; border-bottom: 1px solid rgba(255,255,255,.06); padding: 5px 0; }
    .k { display:block; color: var(--muted); font-size: 12px; }
    .v { display:block; font-size: 15px; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
    .badge { display:inline-flex; min-width: 48px; justify-content:center; padding: 2px 8px; border-radius: 999px; color:#061016; font-size: 12px; font-weight:700; }
    .ok { background: var(--ok); }
    .bad { background: var(--bad); }
    .warn { background: var(--warn); }
    pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; color: #c6d1dc; font-size: 12px; line-height: 1.45; }
    @media (max-width: 900px) { main { height: auto; min-height: 100vh; grid-template-columns: 1fr; } .viewer { aspect-ratio: 16 / 9; } .side { grid-template-rows: auto auto auto; } }
  </style>
</head>
<body>
  <main>
    <section class="stage">
      <div class="topbar">
        <h1>Reading Arm Debug</h1>
        <div class="hint" id="lastUpdate">connecting...</div>
      </div>
      <div class="viewer"><canvas id="canvas"></canvas></div>
    </section>
    <aside class="side">
      <section class="panel">
        <h2>Arm Controls</h2>
        <div class="controls">
          <button id="prepareBtn" data-action="/reading/prepare?timeout=12">准备初始位</button>
          <button id="startBtn" data-action="/reading/start">开始找书/对齐</button>
          <button id="stopBtn" data-action="/reading/stop">停止保持</button>
          <button id="homeBtn" data-action="/reading/stop?return_home=1">回初始位</button>
        </div>
        <div class="control-status" id="controlStatus">idle</div>
      </section>
      <section class="panel">
        <h2>Reading State</h2>
        <div class="state-grid" id="stateGrid"></div>
      </section>
      <section class="panel">
        <h2>Book Detection</h2>
        <div class="state-grid" id="detectGrid"></div>
      </section>
      <section class="panel">
        <h2>Alignment Error</h2>
        <div class="state-grid" id="errorGrid"></div>
      </section>
      <section class="panel">
        <h2>Joint Positions</h2>
        <div class="state-grid" id="jointGrid"></div>
      </section>
      <section class="panel">
        <h2>Raw Debug JSON</h2>
        <pre id="rawJson">{}</pre>
      </section>
    </aside>
  </main>
  <script>
    const canvas = document.getElementById("canvas");
    const ctx = canvas.getContext("2d");
    const stateGrid = document.getElementById("stateGrid");
    const detectGrid = document.getElementById("detectGrid");
    const errorGrid = document.getElementById("errorGrid");
    const jointGrid = document.getElementById("jointGrid");
    const rawJson = document.getElementById("rawJson");
    const lastUpdate = document.getElementById("lastUpdate");
    const controlStatus = document.getElementById("controlStatus");
    let latest = null;
    let lastFrameOk = false;
    const DEBUG_FRAME_WIDTH = 480;
    const FRAME_REFRESH_MS = 500;
    const TARGET = { x: 0.5, y: 0.5, ratio: 0.3 };

    function overlayScale(d) {
      const frame = d && d.frame ? d.frame : { width: canvas.width, height: canvas.height };
      const sx = frame.width ? canvas.width / frame.width : 1;
      const sy = frame.height ? canvas.height / frame.height : 1;
      return { sx, sy };
    }

    function scalePoint(point, scale) {
      return { x: point.x * scale.sx, y: point.y * scale.sy };
    }

    function badge(value) {
      const cls = value ? "ok" : "bad";
      return `<span class="badge ${cls}">${value ? "true" : "false"}</span>`;
    }

    function kv(label, value) {
      return `<div class="kv"><span class="k">${label}</span><span class="v">${value}</span></div>`;
    }

    function fmt(value, digits = 4) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
      return Number(value).toFixed(digits);
    }

    function classifyError(value) {
      const absValue = Math.abs(Number(value));
      if (!Number.isFinite(absValue)) return `<span class="badge warn">--</span>`;
      if (absValue <= 0.12) return `<span class="badge ok">ok</span>`;
      if (absValue <= 0.2) return `<span class="badge warn">near</span>`;
      return `<span class="badge bad">far</span>`;
    }

    function updatePanels(data) {
      const s = data.status || {};
      const d = data.detection || {};
      const p = data.performance || {};
      const j = data.joints || {};
      const debugFrame = p.debug_frame || {};
      stateGrid.innerHTML = [
        kv("found", badge(!!s.found)),
        kv("ready", badge(!!s.ready)),
        kv("settled", badge(!!s.settled)),
        kv("tracking", badge(!!s.tracking)),
        kv("searching", badge(!!s.searching)),
        kv("search_complete", badge(!!s.search_complete)),
        kv("preparing", badge(!!s.preparing)),
        kv("prepare_complete", badge(!!s.prepare_complete))
      ].join("");
      const angle = d.angle_deg === null || d.angle_deg === undefined ? "--" : `${d.angle_deg.toFixed ? d.angle_deg.toFixed(1) : d.angle_deg}°`;
      detectGrid.innerHTML = [
        kv("cx", s.cx ?? "--"),
        kv("cy", s.cy ?? "--"),
        kv("area_ratio", s.area_ratio ?? "--"),
        kv("angle", angle),
        kv("infer_fps", p.inference_fps ?? "--"),
        kv("infer_ms", p.avg_infer_ms ?? "--"),
        kv("num_pages", d.num_pages ?? 0),
        kv("corners", (d.corners || []).length),
        kv("frame", d.frame ? `${d.frame.width} x ${d.frame.height}` : "--"),
        kv("web_frame", debugFrame.width ? `${debugFrame.width} x ${debugFrame.height}` : "--"),
        kv("frame_http", lastFrameOk ? `<span class="badge ok">ok</span>` : `<span class="badge warn">wait</span>`)
      ].join("");
      const ex = Number(s.cx || 0) - TARGET.x;
      const ey = Number(s.cy || 0) - TARGET.y;
      const er = Number(s.area_ratio || 0) - TARGET.ratio;
      errorGrid.innerHTML = [
        kv("e_x", `${fmt(ex)} ${classifyError(ex)}`),
        kv("e_y", `${fmt(ey)} ${classifyError(ey)}`),
        kv("e_ratio", `${fmt(er)} ${classifyError(er)}`),
        kv("target", `${TARGET.x} / ${TARGET.y} / ${TARGET.ratio}`)
      ].join("");
      const joints = j.ordered || [];
      jointGrid.innerHTML = joints.length
        ? joints.map(item => kv(item.name, fmt(item.position))).join("")
        : kv("joint_states", `<span class="badge warn">wait</span>`);
      rawJson.textContent = JSON.stringify(data, null, 2);
      lastUpdate.textContent = new Date().toLocaleTimeString();
    }

    async function postAction(path, label) {
      const buttons = Array.from(document.querySelectorAll("button[data-action]"));
      buttons.forEach(button => { button.disabled = true; });
      controlStatus.textContent = `${label}...`;
      try {
        const res = await fetch(path, { method: "POST", cache: "no-store" });
        controlStatus.textContent = `${label}: HTTP ${res.status}`;
        await refreshStatus();
      } catch (err) {
        controlStatus.textContent = `${label}: ${err}`;
      } finally {
        buttons.forEach(button => { button.disabled = false; });
      }
    }

    document.querySelectorAll("button[data-action]").forEach(button => {
      button.addEventListener("click", () => postAction(button.dataset.action, button.textContent.trim()));
    });

    function resizeCanvasToImage(img) {
      if (canvas.width !== img.naturalWidth || canvas.height !== img.naturalHeight) {
        canvas.width = img.naturalWidth || 1280;
        canvas.height = img.naturalHeight || 720;
      }
    }

    function drawOverlay() {
      const d = latest && latest.detection ? latest.detection : {};
      if (!d.found) {
        ctx.save();
        ctx.fillStyle = "rgba(255, 107, 107, 0.9)";
        ctx.font = "bold 40px system-ui";
        ctx.fillText("NO BOOK", 32, 64);
        ctx.restore();
        return;
      }
      const scale = overlayScale(d);
      const corners = d.corners || [];
      if (corners.length >= 4) {
        ctx.save();
        ctx.lineWidth = 4;
        ctx.strokeStyle = "#f5c542";
        ctx.fillStyle = "#f5c542";
        ctx.beginPath();
        corners.forEach((p, i) => {
          const point = scalePoint(p, scale);
          if (i === 0) ctx.moveTo(point.x, point.y);
          else ctx.lineTo(point.x, point.y);
        });
        ctx.closePath();
        ctx.stroke();
        ctx.font = "bold 14px system-ui";
        corners.forEach(p => {
          const point = scalePoint(p, scale);
          ctx.beginPath();
          ctx.arc(point.x, point.y, 4, 0, Math.PI * 2);
          ctx.fill();
          ctx.fillText(`${p.name} ${p.conf ?? ""}`, point.x + 7, point.y - 7);
        });
        ctx.restore();
      }
      (d.pages || []).forEach((page, i) => {
        if (!page.bbox) return;
        const [x, y, w, h] = page.bbox;
        ctx.save();
        ctx.lineWidth = 2;
        ctx.strokeStyle = i === 0 ? "#4cc9f0" : "#39d98a";
        ctx.strokeRect(x * scale.sx, y * scale.sy, w * scale.sx, h * scale.sy);
        ctx.font = "bold 14px system-ui";
        ctx.fillStyle = ctx.strokeStyle;
        ctx.fillText(`page ${i + 1}`, x * scale.sx + 6, Math.max(18, y * scale.sy - 6));
        ctx.restore();
      });
      if (d.center) {
        const center = scalePoint(d.center, scale);
        ctx.save();
        ctx.fillStyle = "#ffffff";
        ctx.beginPath();
        ctx.arc(center.x, center.y, 4, 0, Math.PI * 2);
        ctx.fill();
        ctx.font = "bold 16px system-ui";
        const angle = d.angle_deg === null || d.angle_deg === undefined ? "--" : `${d.angle_deg.toFixed(1)}°`;
        ctx.fillText(`angle ${angle}`, center.x + 9, center.y - 9);
        ctx.restore();
      }
    }

    function refreshFrame() {
      const img = new Image();
      img.onload = () => {
        lastFrameOk = true;
        resizeCanvasToImage(img);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        drawOverlay();
      };
      img.onerror = () => { lastFrameOk = false; };
      img.src = `/frame.jpg?debug=1&w=${DEBUG_FRAME_WIDTH}&t=${Date.now()}`;
    }

    async function refreshStatus() {
      try {
        const res = await fetch(`/debug/status?t=${Date.now()}`, { cache: "no-store" });
        latest = await res.json();
        updatePanels(latest);
      } catch (err) {
        lastUpdate.textContent = `status error: ${err}`;
      }
    }

    setInterval(refreshStatus, 500);
    setInterval(refreshFrame, FRAME_REFRESH_MS);
    refreshStatus();
    refreshFrame();
  </script>
</body>
</html>"""


def _make_handler(node):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/frame.jpg":
                q = parse_qs(parsed.query)
                debug_frame = q.get("debug", ["0"])[0] == "1"
                if q.get("wait_ready", ["0"])[0] == "1":
                    timeout = _wait_timeout(q)
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline:
                        status = node.snapshot()
                        if status["ready"]:
                            break
                        if not status["tracking"] or status["search_complete"]:
                            break
                        time.sleep(0.02)
                    if not node.snapshot()["ready"]:
                        self.send_response(409)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                jpg = node.latest_jpg(debug=debug_frame)
                if jpg is None:
                    self.send_response(503)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(jpg)))
                self.end_headers()
                self.wfile.write(jpg)
            elif parsed.path == "/book/status":
                body = json.dumps(node.snapshot()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/debug/status":
                body = json.dumps(node.debug_snapshot(), ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/debug":
                body = _debug_page_html().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            parsed = urlparse(self.path)
            if parsed.path == "/reading/prepare":
                timeout = _wait_timeout(parse_qs(parsed.query))
                if node.prepare_reading(timeout=timeout):
                    self._ok()
                else:
                    self.send_response(504)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
            elif parsed.path == "/reading/start":
                node.set_tracking(True)
                self._ok()
            elif parsed.path == "/reading/stop":
                q = parse_qs(parsed.query)
                return_home = q.get("return_home", ["0"])[0] == "1"
                node.set_tracking(False, return_home=return_home)
                self._ok()
            else:
                self.send_response(404)
                self.end_headers()

        def _ok(self):
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    return Handler


class ArmAgent(Node):
    def __init__(self):
        super().__init__("arm_agent")
        self.declare_parameter("model_path", MODEL_PATH)
        self.declare_parameter(
            "camera_device",
            "/dev/v4l/by-path/platform-fc880000.usb-usb-0:1.3:1.0-video-index0",
        )
        self.declare_parameter("camera_index", 21)
        self.declare_parameter("allow_camera_index_fallback", False)
        self.declare_parameter("width", 1280)
        self.declare_parameter("height", 720)
        self.declare_parameter("jpeg_quality", 95)
        self.declare_parameter("debug_frame_width", 480)
        self.declare_parameter("debug_jpeg_quality", 55)
        self.declare_parameter("http_host", "0.0.0.0")
        self.declare_parameter("http_port", 8642)
        self.declare_parameter("settle_sec", 1.0)
        self.declare_parameter("motion_epsilon", 0.005)

        model = self.get_parameter("model_path").value
        self.model_handle = _lib.book_detect_init(model.encode())
        if not self.model_handle:
            self.get_logger().fatal(f"Failed to load model: {model}")
            sys.exit(1)
        self.get_logger().info(f"Model loaded: {model}")

        camera_device = self.get_parameter("camera_device").value
        cam_idx = self.get_parameter("camera_index").value
        allow_fallback = self.get_parameter("allow_camera_index_fallback").value
        camera_source = select_camera_source(
            camera_device,
            cam_idx,
            allow_index_fallback=allow_fallback,
        )
        self.get_logger().info(f"Opening camera source: {camera_source}")
        self.w = self.get_parameter("width").value
        self.h = self.get_parameter("height").value
        self.q = self.get_parameter("jpeg_quality").value
        self.debug_w, self.debug_h = scaled_debug_size(
            self.w,
            self.h,
            self.get_parameter("debug_frame_width").value,
        )
        self.debug_q = int(self.get_parameter("debug_jpeg_quality").value)
        self.cap = cv2.VideoCapture(camera_source, cv2.CAP_V4L2)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.w)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.h)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        if not self.cap.isOpened():
            _lib.book_detect_release(self.model_handle)
            self.model_handle = None
            raise RuntimeError(f"Failed to open camera: {camera_source}")
        self.get_logger().info(f"Camera: {camera_source}")

        # 共享状态（HTTP 线程与 ROS 线程共享，加锁）
        self._lock = threading.Lock()
        self._latest_jpg = None
        self._latest_debug_jpg = None
        self._tracking = False
        self._found = False
        self._cx = 0.0
        self._cy = 0.0
        self._area_ratio = 0.0
        self._book_debug = build_book_debug({"found": False, "num_pages": 0}, self.w, self.h)
        self._last_debug_t = time.time()
        self._searching = False
        self._search_complete = False
        self._preparing = False
        self._prepare_complete = False
        self._joint_names = []
        self._joint_positions = []
        self._settle = MotionSettleTracker(
            epsilon=self.get_parameter("motion_epsilon").value,
            settle_sec=self.get_parameter("settle_sec").value,
        )
        self._infer_stats = InferenceStats()

        self.pub = self.create_publisher(Float32MultiArray, "/face_info", 10)
        self.prepare_pub = self.create_publisher(Bool, "/book_prepare", 10)
        self.return_home_pub = self.create_publisher(Bool, "/book_return_home", 10)
        self.create_subscription(JointState, "/joint_states", self._on_joint_states, 10)
        self.create_subscription(
            Int8, "/book_search_status", self._on_search_status, 10
        )
        self.create_subscription(
            Int8, "/book_prepare_status", self._on_prepare_status, 10
        )
        self.timer = self.create_timer(1.0 / 60.0, self.tick)

        host = self.get_parameter("http_host").value
        port = self.get_parameter("http_port").value
        self._httpd = ThreadingHTTPServer((host, port), _make_handler(self))
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        self.get_logger().info(f"HTTP serving on {host}:{port}")
        self.get_logger().info("ArmAgent ready")

    def _on_joint_states(self, msg: JointState):
        with self._lock:
            self._settle.update(list(msg.position))
            self._joint_names = list(msg.name)
            self._joint_positions = list(msg.position)

    def _on_search_status(self, msg: Int8):
        with self._lock:
            self._searching = msg.data == 1
            self._search_complete = msg.data == 2

    def _on_prepare_status(self, msg: Int8):
        with self._lock:
            self._preparing = msg.data == 1
            self._prepare_complete = msg.data == 2

    def _publish_not_found(self):
        debug = build_book_debug({"found": False, "num_pages": 0}, self.w, self.h)
        with self._lock:
            self._found = False
            self._cx = 0.0
            self._cy = 0.0
            self._area_ratio = 0.0
            self._book_debug = debug
            self._last_debug_t = time.time()
            tracking = self._tracking
        self.pub.publish(Float32MultiArray(
            data=[0.0, 0.0, 0.0, 0.0, 1.0 if tracking else 0.0]
        ))

    def tick(self):
        ret, frame = self.cap.read()
        if not ret:
            return
        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.q])
        if not ok:
            return
        jpg_bytes = jpg.tobytes()
        debug_jpg_bytes = None
        debug_frame = frame
        if (self.debug_w, self.debug_h) != (self.w, self.h):
            debug_frame = cv2.resize(
                frame,
                (self.debug_w, self.debug_h),
                interpolation=cv2.INTER_AREA,
            )
        ok_debug, debug_jpg = cv2.imencode(
            ".jpg",
            debug_frame,
            [cv2.IMWRITE_JPEG_QUALITY, self.debug_q],
        )
        if ok_debug:
            debug_jpg_bytes = debug_jpg.tobytes()
        with self._lock:
            self._latest_jpg = jpg_bytes
            self._latest_debug_jpg = debug_jpg_bytes

        buf = (ctypes.c_ubyte * len(jpg)).from_buffer_copy(jpg_bytes)
        infer_start = time.monotonic()
        ptr = _lib.book_detect_infer(self.model_handle, buf, len(jpg))
        infer_sec = time.monotonic() - infer_start
        with self._lock:
            self._infer_stats.record(infer_sec)
        if not ptr:
            self._publish_not_found()
            return
        try:
            result = json.loads(ctypes.cast(ptr, ctypes.c_char_p).value)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            self._publish_not_found()
            return
        finally:
            _libc.free(ptr)

        center = result.get("center")
        if result.get("found") and isinstance(center, (list, tuple)) and len(center) >= 2:
            cx = center[0] / self.w
            cy = center[1] / self.h
            ar = result.get("area_ratio", 0.0)
            debug = build_book_debug(result, self.w, self.h)
            with self._lock:
                self._found, self._cx, self._cy, self._area_ratio = True, cx, cy, ar
                self._book_debug = debug
                self._last_debug_t = time.time()
                tracking = self._tracking
            if tracking:
                self.pub.publish(Float32MultiArray(
                    data=[float(cx), float(cy), float(ar), 1.0, 1.0]
                ))
            else:
                self.pub.publish(Float32MultiArray(
                    data=[0.0, 0.0, 0.0, 0.0, 0.0]
                ))
        else:
            self._publish_not_found()

    # ── 供 HTTP 线程调用 ──
    def snapshot(self):
        with self._lock:
            settled = self._settle.settled()
            ready = reading_ready(
                self._tracking,
                self._found,
                settled,
                self._searching,
                self._search_complete,
            )
            return {
                "found": self._found,
                "cx": round(self._cx, 4),
                "cy": round(self._cy, 4),
                "area_ratio": round(self._area_ratio, 4),
                "settled": settled,
                "ready": ready,
                "tracking": self._tracking,
                "searching": self._searching,
                "search_complete": self._search_complete,
                "preparing": self._preparing,
                "prepare_complete": self._prepare_complete,
            }

    def debug_snapshot(self):
        status = self.snapshot()
        with self._lock:
            detection = dict(self._book_debug)
            last_debug_t = self._last_debug_t
            performance = self._infer_stats.snapshot()
            performance["debug_frame"] = {
                "width": int(self.debug_w),
                "height": int(self.debug_h),
                "quality": int(self.debug_q),
            }
            performance["source_frame"] = {
                "width": int(self.w),
                "height": int(self.h),
                "quality": int(self.q),
            }
            joints = build_joint_debug(self._joint_names, self._joint_positions)
        return {
            "status": status,
            "detection": detection,
            "joints": joints,
            "performance": performance,
            "debug_age_sec": round(max(0.0, time.time() - last_debug_t), 3),
            "server_time": round(time.time(), 3),
        }

    def latest_jpg(self, debug=False):
        with self._lock:
            if debug and self._latest_debug_jpg is not None:
                return self._latest_debug_jpg
            return self._latest_jpg

    def prepare_reading(self, timeout: float = 8.0) -> bool:
        with self._lock:
            self._tracking = False
            self._searching = False
            self._search_complete = False
            self._preparing = False
            self._prepare_complete = False
            self._settle.reset()
        deadline = time.monotonic() + max(0.1, min(float(timeout), 20.0))
        retry = PrepareCommandRepublisher(interval_sec=0.15, time_fn=time.monotonic)
        while time.monotonic() < deadline:
            status = self.snapshot()
            if status.get("prepare_complete") and status.get("settled"):
                self.get_logger().info("reading prepare complete")
                return True
            if retry.should_publish(
                preparing=bool(status.get("preparing")),
                complete=bool(status.get("prepare_complete")),
            ):
                self.prepare_pub.publish(Bool(data=True))
            time.sleep(0.02)
        self.get_logger().warning(f"reading prepare timed out status={self.snapshot()}")
        return False

    def set_tracking(self, on: bool, return_home: bool = False):
        with self._lock:
            if on:
                self._settle.reset()
            self._searching = False
            self._search_complete = False
            if on:
                self._preparing = False
            self._tracking = on
        if return_home and not on:
            self.return_home_pub.publish(Bool(data=True))
        self.get_logger().info(f"tracking={'ON' if on else 'OFF'}")

    def destroy_node(self):
        if hasattr(self, "_httpd"):
            self._httpd.shutdown()
            self._httpd.server_close()
        if hasattr(self, "cap"):
            self.cap.release()
        if hasattr(self, "model_handle") and self.model_handle:
            _lib.book_detect_release(self.model_handle)
            self.model_handle = None
        return super().destroy_node()


def main():
    rclpy.init()
    node = ArmAgent()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
