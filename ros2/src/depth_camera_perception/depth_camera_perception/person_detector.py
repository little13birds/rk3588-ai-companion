from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

BBox = Tuple[float, float, float, float]


@dataclass(frozen=True)
class PoseKeypoint:
    x: float
    y: float
    confidence: float


@dataclass(frozen=True)
class Detection:
    bbox: BBox
    confidence: float
    class_id: int
    label: str
    track_id: Optional[int] = None
    distance_m: Optional[float] = None
    keypoints: Tuple[PoseKeypoint, ...] = ()
    stable_score: float = 0.0
    age: int = 0
    missed: int = 0

    def with_distance(self, distance_m: Optional[float]) -> Detection:
        return Detection(
            bbox=self.bbox,
            confidence=self.confidence,
            class_id=self.class_id,
            label=self.label,
            track_id=self.track_id,
            distance_m=distance_m,
            keypoints=self.keypoints,
            stable_score=self.stable_score,
            age=self.age,
            missed=self.missed,
        )


@dataclass(frozen=True)
class LetterboxMeta:
    original_width: int
    original_height: int
    input_size: int
    scale: float
    pad_x: int
    pad_y: int


class PersonDetector:
    def detect(self, bgr_image: np.ndarray) -> Sequence[Detection]:
        raise NotImplementedError


def _letterbox_bgr(bgr_image: np.ndarray, input_size: int = 640) -> Tuple[np.ndarray, LetterboxMeta]:
    if bgr_image.ndim != 3 or bgr_image.shape[2] != 3:
        raise ValueError("bgr_image must have shape HxWx3")
    original_height, original_width = bgr_image.shape[:2]
    if original_width <= 0 or original_height <= 0:
        raise ValueError("bgr_image must not be empty")

    scale = min(input_size / original_width, input_size / original_height)
    resized_width = int(round(original_width * scale))
    resized_height = int(round(original_height * scale))
    pad_x = (input_size - resized_width) // 2
    pad_y = (input_size - resized_height) // 2

    if resized_width == original_width and resized_height == original_height:
        resized = bgr_image.copy()
    else:
        resized = cv2.resize(bgr_image, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)

    canvas = np.full((input_size, input_size, 3), 114, dtype=np.uint8)
    canvas[pad_y : pad_y + resized_height, pad_x : pad_x + resized_width] = resized
    meta = LetterboxMeta(
        original_width=original_width,
        original_height=original_height,
        input_size=input_size,
        scale=scale,
        pad_x=pad_x,
        pad_y=pad_y,
    )
    return canvas, meta


def _prepare_rknn_input(bgr_image: np.ndarray, input_size: int = 640) -> Tuple[np.ndarray, LetterboxMeta]:
    letterboxed, meta = _letterbox_bgr(bgr_image, input_size=input_size)
    rgb = cv2.cvtColor(letterboxed, cv2.COLOR_BGR2RGB)
    tensor = np.expand_dims(rgb, axis=0).astype(np.uint8, copy=False)
    return tensor, meta


def _scale_box_from_letterbox(box: BBox, meta: LetterboxMeta) -> BBox:
    x1, y1, x2, y2 = box
    x1 = (x1 - meta.pad_x) / meta.scale
    x2 = (x2 - meta.pad_x) / meta.scale
    y1 = (y1 - meta.pad_y) / meta.scale
    y2 = (y2 - meta.pad_y) / meta.scale
    x1 = float(max(0.0, min(float(meta.original_width), x1)))
    x2 = float(max(0.0, min(float(meta.original_width), x2)))
    y1 = float(max(0.0, min(float(meta.original_height), y1)))
    y2 = float(max(0.0, min(float(meta.original_height), y2)))
    return (x1, y1, x2, y2)


def _normalise_yolov8_output(output: np.ndarray) -> np.ndarray:
    predictions = np.asarray(output)
    if predictions.ndim == 3 and predictions.shape[0] == 1:
        predictions = predictions[0]
    if predictions.ndim != 2:
        raise ValueError(f"expected YOLOv8 output with 2 or 3 dims, got {predictions.shape}")
    if predictions.shape[0] >= 5 and predictions.shape[1] > predictions.shape[0]:
        predictions = predictions.T
    elif predictions.shape[1] < 5:
        raise ValueError(f"expected YOLOv8 prediction rows with at least 5 values, got {predictions.shape}")
    return predictions.astype(np.float32, copy=False)


def _iou(a: BBox, b: BBox) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter_area
    if union <= 0.0:
        return 0.0
    return inter_area / union


def _nms(detections: Sequence[Detection], threshold: float, max_detections: int) -> List[Detection]:
    remaining = sorted(detections, key=lambda detection: detection.confidence, reverse=True)
    kept: List[Detection] = []
    while remaining and len(kept) < max_detections:
        current = remaining.pop(0)
        kept.append(current)
        remaining = [
            detection
            for detection in remaining
            if _iou(current.bbox, detection.bbox) <= threshold
        ]
    return kept


def _decode_yolov8_output(
    output: np.ndarray,
    meta: LetterboxMeta,
    confidence: float,
    nms_threshold: float = 0.45,
    max_detections: int = 50,
) -> List[Detection]:
    predictions = _normalise_yolov8_output(output)
    if predictions.shape[1] < 5:
        return []

    class_scores = predictions[:, 4:]
    if class_scores.shape[1] == 0:
        return []
    person_scores = class_scores[:, 0]
    candidate_indices = np.where(person_scores >= confidence)[0]

    detections: List[Detection] = []
    for index in candidate_indices:
        cx, cy, width, height = [float(value) for value in predictions[index, :4]]
        if width <= 0.0 or height <= 0.0:
            continue
        model_box = (
            cx - width / 2.0,
            cy - height / 2.0,
            cx + width / 2.0,
            cy + height / 2.0,
        )
        bbox = _scale_box_from_letterbox(model_box, meta)
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        detections.append(
            Detection(
                bbox=bbox,
                confidence=float(person_scores[index]),
                class_id=0,
                label="person",
            )
        )

    return _nms(detections, threshold=nms_threshold, max_detections=max_detections)


class UltralyticsPersonDetector(PersonDetector):
    def __init__(self, model_path: str = "yolov8n.pt", confidence: float = 0.4):
        try:
            from ultralytics import YOLO
        except Exception as exc:
            raise RuntimeError(
                "ultralytics is required for detector_backend=ultralytics; "
                "install it or switch to a different detector backend"
            ) from exc
        self._model = YOLO(model_path)
        self._confidence = confidence

    def detect(self, bgr_image: np.ndarray) -> List[Detection]:
        results = self._model.predict(bgr_image, conf=self._confidence, verbose=False)
        detections: List[Detection] = []
        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue
            for box in boxes:
                class_id = int(box.cls[0].item())
                if class_id != 0:
                    continue
                x1, y1, x2, y2 = [float(v) for v in box.xyxy[0].tolist()]
                detections.append(
                    Detection(
                        bbox=(x1, y1, x2, y2),
                        confidence=float(box.conf[0].item()),
                        class_id=0,
                        label="person",
                    )
                )
        return detections


class RknnYoloV8PersonDetector(PersonDetector):
    def __init__(
        self,
        model_path: str,
        confidence: float = 0.4,
        input_size: int = 640,
        nms_threshold: float = 0.45,
    ):
        try:
            from rknnlite.api import RKNNLite
        except Exception as exc:
            raise RuntimeError(
                "rknnlite is required for detector_backend=rknn; "
                "install rknn-toolkit-lite2 on the RK3588 board"
            ) from exc

        self._input_size = input_size
        self._confidence = confidence
        self._nms_threshold = nms_threshold
        self._rknn = RKNNLite()
        ret = self._rknn.load_rknn(model_path)
        if ret != 0:
            raise RuntimeError(f"failed to load RKNN model {model_path}: ret={ret}")
        ret = self._rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_0)
        if ret != 0:
            self._rknn.release()
            raise RuntimeError(f"failed to initialize RKNN runtime: ret={ret}")

    def detect(self, bgr_image: np.ndarray) -> List[Detection]:
        tensor, meta = _prepare_rknn_input(bgr_image, input_size=self._input_size)
        outputs = self._rknn.inference(inputs=[tensor], data_type="uint8", data_format="nhwc")
        if not outputs:
            return []
        return _decode_yolov8_output(
            outputs[0],
            meta,
            confidence=self._confidence,
            nms_threshold=self._nms_threshold,
        )

    def close(self) -> None:
        if self._rknn is not None:
            self._rknn.release()
            self._rknn = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def create_person_detector(
    backend: str,
    model_path: str,
    confidence: float,
    **kwargs,
) -> PersonDetector:
    if backend == "ultralytics":
        return UltralyticsPersonDetector(model_path=model_path, confidence=confidence)
    if backend in {"rknn", "rknnlite"}:
        return RknnYoloV8PersonDetector(model_path=model_path, confidence=confidence)
    if backend in {"pose_rknn", "pose"}:
        from .pose_person_detector import PoseRknnPersonDetector

        return PoseRknnPersonDetector(
            model_path=model_path,
            confidence=confidence,
            face_identity_root=kwargs.get("face_identity_root", "/home/elf/face_identity_rk3588"),
            pose_lib_path=kwargs.get("pose_lib_path"),
            native=kwargs.get("native"),
        )
    raise ValueError(f"unsupported detector backend: {backend}")
