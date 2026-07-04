"""arm_agent HTTP 客户端 — cloud-model 向 ROS2 侧 arm_agent 取帧/控制读书跟踪。

所有调用失败时优雅降级：取帧/状态返回 None，控制返回 False，绝不抛异常。
"""
import json
import urllib.error
import urllib.request

from config import ARM_AGENT_URL


def _get(path: str, timeout: float):
    with urllib.request.urlopen(ARM_AGENT_URL + path, timeout=timeout) as resp:
        return resp.status, resp.read()


def _post(path: str, timeout: float = 2.0) -> bool:
    req = urllib.request.Request(ARM_AGENT_URL + path, data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def get_frame(wait_ready: bool = False, timeout: float = None):
    """取最新 JPEG 帧 (bytes)；失败返回 None。
    wait_ready=True (读书模式): 请求 agent 等机械臂对齐且舵机静止后再交帧。"""
    if wait_ready:
        wait_sec = 25.0 if timeout is None else max(0.0, min(timeout, 35.0))
        path = f"/frame.jpg?wait_ready=1&timeout={wait_sec:g}"
        http_timeout = wait_sec + 3.0
    else:
        path = "/frame.jpg"
        http_timeout = 3.0 if timeout is None else timeout
    try:
        status, body = _get(path, timeout=http_timeout)
        return body if (status == 200 and body) else None
    except (urllib.error.URLError, OSError):
        return None


def get_status():
    """取书本/机械臂状态；含搜索、检测、静止和就绪状态，失败返回 None。"""
    try:
        status, body = _get("/book/status", timeout=2.0)
        return json.loads(body.decode()) if status == 200 else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def prepare_reading() -> bool:
    """通知 arm_agent 先回到读书初始姿态。成功 True。"""
    return _post("/reading/prepare?timeout=12", timeout=14.0)


def start_reading() -> bool:
    """通知 arm_agent 开始视觉跟踪书本。成功 True。"""
    return _post("/reading/start")


def stop_reading(return_home: bool = False) -> bool:
    """通知 arm_agent 停止跟踪。

    return_home=True 用于退出读书模式，让机械臂回到读书初始位姿。
    return_home=False 用于页间暂停，机械臂保持当前姿态等待下一页。
    """
    path = "/reading/stop?return_home=1" if return_home else "/reading/stop"
    return _post(path)


def health(require_frame: bool = True, timeout: float = 1.5):
    """Return a structured arm_agent health snapshot.

    This is intentionally side-effect free. It only probes HTTP status and,
    when requested, a lightweight frame fetch so callers can fail fast before
    entering reading mode.
    """
    status_data = None
    errors = []
    try:
        status, body = _get("/book/status", timeout=timeout)
        if status == 200:
            status_data = json.loads(body.decode())
        else:
            errors.append(f"status_http_{status}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        errors.append(f"status:{type(exc).__name__}")

    frame_ok = True
    if require_frame:
        frame_ok = get_frame(wait_ready=False, timeout=timeout) is not None
        if not frame_ok:
            errors.append("frame_unavailable")

    status_ok = isinstance(status_data, dict)
    return {
        "ok": bool(status_ok and (frame_ok or not require_frame)),
        "status_ok": status_ok,
        "frame_ok": bool(frame_ok),
        "require_frame": bool(require_frame),
        "status": status_data or {},
        "errors": errors,
    }
