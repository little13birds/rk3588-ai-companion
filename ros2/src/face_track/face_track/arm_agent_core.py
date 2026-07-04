"""arm_agent pure helpers. No ROS / cv2 dependency, safe for unit tests."""
import math
import os
import time


CORNER_ORDER = ("tl", "tr", "br", "bl")


def scaled_debug_size(width, height, max_width):
    """Return a low-resolution debug frame size preserving aspect ratio."""
    width = int(width)
    height = int(height)
    max_width = int(max_width)
    if width <= 0 or height <= 0 or max_width <= 0 or width <= max_width:
        return width, height
    scale = max_width / float(width)
    return max_width, max(1, int(round(height * scale)))


class InferenceStats:
    """Track detector inference FPS and latency for debug status."""

    def __init__(self, time_fn=time.monotonic, report_interval=1.0, alpha=0.3):
        self._time_fn = time_fn
        self._report_interval = float(report_interval)
        self._alpha = float(alpha)
        self._window_start = time_fn()
        self._window_count = 0
        self._total_count = 0
        self._fps = 0.0
        self._last_ms = 0.0
        self._avg_ms = 0.0

    def record(self, duration_sec):
        now = self._time_fn()
        duration_ms = max(0.0, float(duration_sec) * 1000.0)
        self._last_ms = duration_ms
        if self._avg_ms <= 0.0:
            self._avg_ms = duration_ms
        else:
            self._avg_ms = self._alpha * duration_ms + (1.0 - self._alpha) * self._avg_ms

        self._window_count += 1
        self._total_count += 1
        elapsed = now - self._window_start
        if elapsed >= self._report_interval and elapsed > 0:
            self._fps = self._window_count / elapsed
            self._window_start = now
            self._window_count = 0

    def snapshot(self):
        return {
            "inference_fps": round(self._fps, 2),
            "last_infer_ms": round(self._last_ms, 1),
            "avg_infer_ms": round(self._avg_ms, 1),
            "inference_count": int(self._total_count),
        }


def _float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_float(value, digits=4):
    value = _float_or_none(value)
    return None if value is None else round(value, digits)


def _extract_center(result, width, height):
    center = result.get("center") if isinstance(result, dict) else None
    if not isinstance(center, (list, tuple)) or len(center) < 2:
        return None
    x = _float_or_none(center[0])
    y = _float_or_none(center[1])
    if x is None or y is None:
        return None
    return {
        "x": round(x, 2),
        "y": round(y, 2),
        "nx": round(x / float(width), 4) if width else None,
        "ny": round(y / float(height), 4) if height else None,
    }


def _extract_corners(result):
    corners = result.get("corners") if isinstance(result, dict) else None
    if not isinstance(corners, dict):
        return []
    out = []
    for name in CORNER_ORDER:
        value = corners.get(name)
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            continue
        x = _float_or_none(value[0])
        y = _float_or_none(value[1])
        if x is None or y is None:
            continue
        conf = _float_or_none(value[2]) if len(value) >= 3 else None
        out.append({
            "name": name,
            "x": round(x, 2),
            "y": round(y, 2),
            "conf": None if conf is None else round(conf, 3),
        })
    return out


def _book_angle_deg(corners):
    by_name = {corner["name"]: corner for corner in corners}
    tl = by_name.get("tl")
    tr = by_name.get("tr")
    if not tl or not tr:
        return None
    dx = tr["x"] - tl["x"]
    dy = tr["y"] - tl["y"]
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return None
    return round(math.degrees(math.atan2(dy, dx)), 1)


def _sanitize_pages(result):
    pages = result.get("pages") if isinstance(result, dict) else None
    if not isinstance(pages, list):
        return []
    out = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        item = {}
        if "conf" in page:
            item["conf"] = _round_float(page.get("conf"), 3)
        center = page.get("center")
        if isinstance(center, (list, tuple)) and len(center) >= 2:
            x = _float_or_none(center[0])
            y = _float_or_none(center[1])
            if x is not None and y is not None:
                item["center"] = [round(x, 2), round(y, 2)]
        bbox = page.get("bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            values = [_float_or_none(v) for v in bbox[:4]]
            if all(v is not None for v in values):
                item["bbox"] = [round(v, 2) for v in values]
        corners = _extract_corners(page)
        if corners:
            item["corners"] = corners
        if item:
            out.append(item)
    return out


def build_book_debug(result, width, height):
    """Build JSON-serializable debug metadata from the detector result."""
    result = result if isinstance(result, dict) else {}
    found = bool(result.get("found"))
    corners = _extract_corners(result) if found else []
    return {
        "found": found,
        "frame": {"width": int(width), "height": int(height)},
        "center": _extract_center(result, width, height) if found else None,
        "area_ratio": _round_float(result.get("area_ratio"), 4) if found else 0.0,
        "num_pages": int(result.get("num_pages", 0) or 0),
        "corners": corners,
        "pages": _sanitize_pages(result) if found else [],
        "angle_deg": _book_angle_deg(corners),
    }


def build_joint_debug(names, positions):
    """Build JSON-serializable joint metadata for the debug page."""
    names = list(names or [])
    positions = list(positions or [])
    ordered = []
    by_name = {}
    for index, raw_position in enumerate(positions):
        name = names[index] if index < len(names) and names[index] else f"joint_{index + 1}"
        position = _round_float(raw_position, 4)
        if position is None:
            continue
        item = {"name": str(name), "position": position}
        ordered.append(item)
        by_name[item["name"]] = position
    return {
        "count": len(ordered),
        "ordered": ordered,
        "positions": by_name,
    }


def select_camera_source(camera_device: str, camera_index: int,
                         exists_fn=os.path.exists,
                         allow_index_fallback=True):
    """Prefer a stable V4L by-id path, optionally falling back to an index."""
    if camera_device and exists_fn(camera_device):
        return camera_device
    if camera_device and not allow_index_fallback:
        raise FileNotFoundError(
            f"configured camera_device does not exist: {camera_device}"
        )
    return camera_index


def reading_ready(tracking: bool, found: bool, settled: bool,
                  searching: bool, search_complete: bool) -> bool:
    """Only expose a reading frame after a successful search and alignment."""
    return (
        tracking
        and found
        and settled
        and not searching
        and not search_complete
    )


class PrepareCommandRepublisher:
    """Gate repeated reading-prepare commands until the servo acknowledges them."""

    def __init__(self, interval_sec=0.15, time_fn=time.monotonic):
        self._interval_sec = max(0.02, float(interval_sec))
        self._time_fn = time_fn
        self._next_publish_at = time_fn()

    def should_publish(self, preparing: bool, complete: bool) -> bool:
        if preparing or complete:
            return False
        now = self._time_fn()
        if now < self._next_publish_at:
            return False
        self._next_publish_at = now + self._interval_sec
        return True


class MotionSettleTracker:
    """跟踪关节指令流，判定舵机是否连续静止达 settle_sec。

    用法：每收到一帧 /joint_states 调 update(positions)；查询 settled()。
    任一关节相邻帧位置变化超 epsilon 即视为运动，刷新计时。
    """

    def __init__(self, epsilon: float = 0.005, settle_sec: float = 1.0,
                 time_fn=time.monotonic):
        self.epsilon = epsilon
        self.settle_sec = settle_sec
        self._time_fn = time_fn
        self._last_pos = None
        self._last_motion_t = time_fn()

    def update(self, positions):
        """喂入一组关节位置 list[float]。"""
        now = self._time_fn()
        positions = list(positions)
        if self._last_pos is None:
            self._last_pos = positions
            self._last_motion_t = now
            return
        moved = (
            len(positions) != len(self._last_pos)
            or any(abs(a - b) > self.epsilon
                   for a, b in zip(positions, self._last_pos))
        )
        if moved:
            self._last_motion_t = now
        self._last_pos = positions

    def reset(self):
        """开始一次新的跟踪周期，重新等待首帧关节状态和静止窗口。"""
        self._last_pos = None
        self._last_motion_t = self._time_fn()

    def settled(self) -> bool:
        """距上次运动是否已达 settle_sec。"""
        return (
            self._last_pos is not None
            and (self._time_fn() - self._last_motion_t) >= self.settle_sec
        )


class InitialPoseController:
    """Move joints toward a fixed initial pose without overshooting."""

    def __init__(self, target, max_delta, tolerance: float = 0.002):
        if len(target) != len(max_delta):
            raise ValueError("target and max_delta must have the same length")
        if any(delta <= 0 for delta in max_delta):
            raise ValueError("max_delta values must be positive")
        self.target = [float(v) for v in target]
        self.max_delta = [float(v) for v in max_delta]
        self.tolerance = float(tolerance)

    def advance(self, current):
        if len(current) != len(self.target):
            raise ValueError("current must match target length")
        next_pos = []
        done = True
        for value, target, max_delta in zip(current, self.target, self.max_delta):
            value = float(value)
            diff = target - value
            if abs(diff) <= self.tolerance:
                next_value = target
            else:
                done = False
                step = max(-max_delta, min(max_delta, diff))
                next_value = value + step
                if abs(target - next_value) <= self.tolerance:
                    next_value = target
            next_pos.append(round(float(next_value), 6))
        done = all(abs(a - b) <= self.tolerance for a, b in zip(next_pos, self.target))
        if done:
            next_pos = [round(v, 6) for v in self.target]
        return next_pos, done


class BaseSweepSearch:
    """底座单轮扫描状态机：当前位置 -> 右限位 -> 左限位 -> 起点。"""

    def __init__(self, min_pos: float = -1.57, max_pos: float = 1.57,
                 step: float = 0.04):
        if min_pos >= max_pos:
            raise ValueError("min_pos must be less than max_pos")
        if step <= 0:
            raise ValueError("step must be positive")
        self.min_pos = min_pos
        self.max_pos = max_pos
        self.step = step
        self.active = False
        self._targets = []
        self._target_index = 0

    def start(self, current_pos: float):
        """从当前位置开始一轮扫描。"""
        start_pos = max(self.min_pos, min(self.max_pos, current_pos))
        self._targets = [self.max_pos, self.min_pos, start_pos]
        self._target_index = 0
        self.active = True

    def stop(self):
        self.active = False
        self._targets = []
        self._target_index = 0

    def advance(self, current_pos: float):
        """推进一步，返回 (next_pos, completed)。"""
        if not self.active:
            return current_pos, False

        target = self._targets[self._target_index]
        delta = target - current_pos
        if abs(delta) <= self.step:
            next_pos = target
            self._target_index += 1
            if self._target_index >= len(self._targets):
                self.stop()
                return next_pos, True
            return next_pos, False

        direction = 1.0 if delta > 0 else -1.0
        return current_pos + direction * self.step, False
