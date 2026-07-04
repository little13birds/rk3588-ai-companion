"""HTTP server for the parent dashboard."""
from __future__ import annotations

import json
import mimetypes
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs, urlparse

from .state import DashboardState

_DASHBOARD_HTML = Path(__file__).resolve().parent / "parent-dashboard.html"
_CLIENT_STATE_JS = Path(__file__).resolve().parent / "client_state.js"
_V3_DASHBOARD_DIR = Path(__file__).resolve().parent / "v3_static"
_MAX_JSON_BODY = 5 * 1024 * 1024


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


class DashboardServer:
    def __init__(self, state: DashboardState, host: str = "0.0.0.0", port: int = 8080):
        self.state = state
        self.host = host
        self.port = int(port)
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        if self._httpd:
            return True
        handler = self._make_handler(self.state)
        try:
            self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        except OSError as exc:
            print(
                f"[dashboard] event=start_failed host={self.host} port={self.port} error={exc}",
                flush=True,
            )
            return False
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="dashboard-http", daemon=True)
        self._thread.start()
        self.port = int(self._httpd.server_address[1])
        print(f"[dashboard] event=started url=http://{self.host}:{self.port}", flush=True)
        return True

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    @staticmethod
    def _make_handler(state: DashboardState):
        class Handler(BaseHTTPRequestHandler):
            server_version = "XiaozhiDashboard/0.1"

            def log_message(self, fmt: str, *args: Any) -> None:
                if os.environ.get("DASHBOARD_HTTP_LOG", "0") not in {"1", "true", "yes"}:
                    return
                super().log_message(fmt, *args)

            def _cors(self) -> None:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Expose-Headers", "X-Camera-Source")
                self.send_header("Cache-Control", "no-store")

            def _json(self, data: Dict[str, Any] | list, status: int = 200) -> None:
                raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self._cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _image(self, data: bytes, source: str = "") -> None:
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "image/jpeg")
                if source:
                    self.send_header("X-Camera-Source", source)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _html(self, path: Path, *, deprecated: bool = False) -> None:
                if not path.exists():
                    self._json({"ok": False, "error": "dashboard_html_not_found"}, status=404)
                    return
                raw = path.read_bytes()
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                if deprecated:
                    self.send_header("X-Dashboard-Deprecated", "legacy")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _javascript(self, path: Path) -> None:
                if not path.exists():
                    self._json({"ok": False, "error": "javascript_not_found"}, status=404)
                    return
                raw = path.read_bytes()
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _static(self, root: Path, rel_path: str) -> None:
                root = root.resolve()
                rel_path = (rel_path or "index.html").lstrip("/")
                target = (root / rel_path).resolve()
                if root not in target.parents and target != root:
                    self._json({"ok": False, "error": "static_path_forbidden"}, status=403)
                    return
                if target.is_dir():
                    target = target / "index.html"
                if not target.exists() or not target.is_file():
                    self._json({"ok": False, "error": "static_not_found"}, status=404)
                    return
                raw = target.read_bytes()
                content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                if target.suffix == ".js":
                    content_type = "application/javascript"
                elif target.suffix == ".css":
                    content_type = "text/css"
                elif target.suffix == ".html":
                    content_type = "text/html"
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", f"{content_type}; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _body(self) -> Dict[str, Any]:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0:
                    return {}
                raw = self.rfile.read(min(length, _MAX_JSON_BODY))
                try:
                    data = json.loads(raw.decode("utf-8"))
                    return data if isinstance(data, dict) else {}
                except Exception:
                    return {}

            def do_OPTIONS(self) -> None:
                self.send_response(204)
                self._cors()
                self.end_headers()

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                path = parsed.path
                query = parse_qs(parsed.query)
                try:
                    if path in {"/", "/index.html"}:
                        self._static(_V3_DASHBOARD_DIR, "index.html")
                    elif path in {"/parent-dashboard.html", "/legacy-dashboard", "/legacy-dashboard/"}:
                        self._html(_DASHBOARD_HTML, deprecated=True)
                    elif path in {"/v3-dashboard", "/v3-dashboard/"}:
                        self._static(_V3_DASHBOARD_DIR, "index.html")
                    elif path.startswith("/v3-dashboard/"):
                        self._static(_V3_DASHBOARD_DIR, path[len("/v3-dashboard/") :])
                    elif path == "/client_state.js":
                        self._javascript(_CLIENT_STATE_JS)
                    elif path == "/api/health":
                        self._json({"status": "ok", "service": "cloud-model-dashboard"})
                    elif path == "/api/config":
                        self._json(state.v3_config())
                    elif path == "/api/status":
                        self._json(state.v3_status())
                    elif path == "/api/system/components":
                        self._json(state.v3_system_components())
                    elif path == "/api/history":
                        date = (query.get("date") or [""])[0]
                        self._json(state.v3_history(date))
                    elif path == "/api/history/gallery":
                        self._json(state.v3_history_gallery(
                            category=(query.get("category") or [""])[0],
                            date_from=(query.get("from") or [""])[0],
                            date_to=(query.get("to") or [""])[0],
                        ))
                    elif path == "/api/camera/source":
                        self._json(state.camera_source())
                    elif path == "/api/camera/snapshot":
                        jpg, source = state.camera_snapshot_with_source()
                        if jpg:
                            self._image(jpg, source=source)
                        else:
                            self._json({
                                "ok": False,
                                "error": "snapshot_unavailable",
                                "source": source,
                            }, status=503)
                    elif path == "/api/child/status":
                        self._json(state.child_status())
                    elif path == "/api/environment":
                        self._json(state.environment())
                    elif path == "/api/conversation/summary":
                        self._json(state.conversation_summary())
                    elif path == "/api/reading/report":
                        self._json(state.reading_report())
                    elif path == "/api/reading/records":
                        self._json(state.reading_records())
                    elif path == "/api/activity":
                        self._json(state.activity())
                    elif path == "/api/dashboard/events":
                        date = (query.get("date") or [""])[0] or None
                        kind = (query.get("kind") or [""])[0]
                        try:
                            limit = int((query.get("limit") or ["100"])[0])
                        except ValueError:
                            limit = 100
                        kinds = [kind] if kind else None
                        self._json(state.dashboard_events(date_key=date, limit=limit, kinds=kinds))
                    elif path == "/api/dashboard/timeline":
                        date = (query.get("date") or [""])[0] or None
                        try:
                            limit = int((query.get("limit") or ["100"])[0])
                        except ValueError:
                            limit = 100
                        self._json(state.dashboard_timeline(date_key=date, limit=limit))
                    elif path == "/api/alerts":
                        self._json(state.alerts())
                    elif path == "/api/safety/status":
                        self._json(state.safety_status())
                    elif path == "/api/system/mode":
                        self._json(state.system_mode())
                    elif path == "/api/system/resources":
                        self._json(state.system_resources())
                    elif path == "/api/system/conflicts":
                        self._json(state.system_conflicts())
                    elif path == "/api/system/features":
                        self._json(state.system_features())
                    elif path == "/api/people":
                        self._json(state.people_registry())
                    elif path == "/api/person-task/status":
                        self._json(state.person_task_status())
                    elif path == "/api/sleep/status":
                        self._json(state.sleep_status())
                    elif path == "/api/sleep/children":
                        self._json(state.sleep_children())
                    elif path == "/api/camera/history":
                        date = (query.get("date") or [""])[0]
                        self._json(state.camera_history(date))
                    elif path.startswith("/api/camera/history/image/"):
                        rel = path[len("/api/camera/history/image/") :]
                        image_path = state.resolve_history_image(rel)
                        if not image_path:
                            self._json({"ok": False, "error": "image_not_found"}, status=404)
                        else:
                            self._image(image_path.read_bytes())
                    else:
                        self._json({"ok": False, "error": "not_found"}, status=404)
                except (BrokenPipeError, ConnectionResetError):
                    print(
                        f"[dashboard] event=client_disconnected method=GET path={path}",
                        flush=True,
                    )
                except Exception as exc:
                    print(
                        f"[dashboard] event=request_failed method=GET path={path} "
                        f"error_type={type(exc).__name__} error={exc}",
                        flush=True,
                    )
                    self._json({"ok": False, "error": "internal_error"}, status=500)

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                path = parsed.path
                body = self._body()
                try:
                    if path == "/api/config":
                        self._json(state.update_v3_config(body))
                    elif path == "/api/message/send":
                        ok = state.queue_speech(str(body.get("text") or ""), source="parent")
                        self._json({"ok": ok})
                    elif path == "/api/move":
                        direction = str(body.get("direction") or "stop")
                        self._json(state.request_move(direction))
                    elif path == "/api/move/find-child":
                        self._json(state.request_find_child(
                            target=str(body.get("target") or body.get("unique_name") or "nearest"),
                            timeout_sec=int(body.get("timeout_sec") or 60),
                        ))
                    elif path == "/api/move/emergency-stop":
                        self._json(state.request_emergency_stop())
                    elif path == "/api/people/candidates/upload":
                        self._json(state.people_candidates_from_upload(body))
                    elif path == "/api/people/candidates/capture":
                        self._json(state.people_candidates_from_camera())
                    elif path == "/api/people/enroll":
                        self._json(state.enroll_person(body))
                    elif path == "/api/people/delete":
                        self._json(state.delete_person(body))
                    elif path == "/api/person-task/seek":
                        self._json(state.request_person_seek(body))
                    elif path == "/api/person-task/stop":
                        self._json(state.request_person_stop())
                    elif path == "/api/sleep/settings":
                        self._json(state.update_sleep_settings(body))
                    elif path == "/api/sleep/presence":
                        self._json(state.update_sleep_presence(body))
                    elif path == "/api/sleep/aid/start":
                        self._json(state.start_sleep_aid(body))
                    elif path == "/api/sleep/aid/stop":
                        self._json(state.stop_sleep_aid())
                    elif path == "/api/sleep/remind":
                        ok = state.remind_sleep(str(body.get("text") or ""))
                        self._json({"ok": ok, "sleep": state.sleep_status()})
                    else:
                        self._json({"ok": False, "error": "not_found"}, status=404)
                except (BrokenPipeError, ConnectionResetError):
                    print(
                        f"[dashboard] event=client_disconnected method=POST path={path}",
                        flush=True,
                    )
                except Exception as exc:
                    print(
                        f"[dashboard] event=request_failed method=POST path={path} "
                        f"error_type={type(exc).__name__} error={exc}",
                        flush=True,
                    )
                    self._json({"ok": False, "error": "internal_error"}, status=500)

        return Handler


def start_dashboard_server(state: DashboardState) -> Optional[DashboardServer]:
    if not _bool_env("DASHBOARD_ENABLED", True):
        print("[dashboard] event=disabled", flush=True)
        return None
    host = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
    try:
        port = int(os.environ.get("DASHBOARD_PORT", "8080"))
    except ValueError:
        port = 8080
    server = DashboardServer(state, host=host, port=port)
    return server if server.start() else None
