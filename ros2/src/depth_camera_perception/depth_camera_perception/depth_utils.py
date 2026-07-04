from __future__ import annotations

from typing import Iterable, Optional, Tuple

import numpy as np

BBox = Tuple[float, float, float, float]
PixelRoi = Tuple[int, int, int, int]


def normalize_depth_to_meters(depth_image: np.ndarray) -> np.ndarray:
    if depth_image.dtype == np.uint16:
        return depth_image.astype(np.float32) / 1000.0
    return depth_image.astype(np.float32)


def bbox_center_roi(
    bbox: BBox,
    width: int,
    height: int,
    fraction: float = 0.5,
) -> PixelRoi:
    x1, y1, x2, y2 = bbox
    x1 = max(0.0, min(float(width), x1))
    y1 = max(0.0, min(float(height), y1))
    x2 = max(0.0, min(float(width), x2))
    y2 = max(0.0, min(float(height), y2))
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1

    box_w = max(1.0, x2 - x1)
    box_h = max(1.0, y2 - y1)
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    roi_w = max(1.0, box_w * fraction)
    roi_h = max(1.0, box_h * fraction)

    rx1 = int(max(0, round(cx - roi_w / 2.0)))
    ry1 = int(max(0, round(cy - roi_h / 2.0)))
    rx2 = int(min(width, round(cx + roi_w / 2.0)))
    ry2 = int(min(height, round(cy + roi_h / 2.0)))
    return rx1, ry1, max(rx1 + 1, rx2), max(ry1 + 1, ry2)


def estimate_bbox_depth_m(
    depth_image: np.ndarray,
    bbox: BBox,
    fraction: float = 0.5,
    min_depth_m: float = 0.2,
    max_depth_m: float = 6.0,
) -> Optional[float]:
    depth_m = normalize_depth_to_meters(depth_image)
    height, width = depth_m.shape[:2]
    x1, y1, x2, y2 = bbox_center_roi(bbox, width=width, height=height, fraction=fraction)
    samples = depth_m[y1:y2, x1:x2]
    valid = samples[np.isfinite(samples)]
    valid = valid[(valid >= min_depth_m) & (valid <= max_depth_m)]
    if valid.size == 0:
        return None
    return float(np.median(valid))


def nearest_detection(detections: Iterable[object]) -> Optional[object]:
    valid = [item for item in detections if getattr(item, "distance_m", None) is not None]
    if not valid:
        return None
    return min(valid, key=lambda item: item.distance_m)
