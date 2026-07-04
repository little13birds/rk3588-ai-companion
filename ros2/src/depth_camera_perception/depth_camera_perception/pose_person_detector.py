from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .person_detector import Detection, PersonDetector, PoseKeypoint


class PoseRknnPersonDetector(PersonDetector):
    def __init__(
        self,
        model_path: str = "/home/elf/face_identity_rk3588/models/rknn/pose_yolov8n_hybrid.rknn",
        confidence: float = 0.35,
        *,
        face_identity_root: str | Path = "/home/elf/face_identity_rk3588",
        pose_lib_path: Optional[str | Path] = None,
        native=None,
    ):
        self._confidence = float(confidence)
        if native is not None:
            self._native = native
            return

        root = Path(face_identity_root).expanduser()
        src_dir = root / "src"
        if str(src_dir) not in sys.path:
            sys.path.insert(0, str(src_dir))

        from face_identity.paths import DEFAULT_PERSON_POSE_LIB
        from face_identity.person_pose_runtime import PersonPoseNative

        self._native = PersonPoseNative(
            lib_path=pose_lib_path or DEFAULT_PERSON_POSE_LIB,
            pose_path=model_path,
            pose_conf=self._confidence,
        )

    def detect(self, bgr_image: np.ndarray) -> list[Detection]:
        ok, encoded = cv2.imencode(".jpg", bgr_image, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            return []
        result = self._native.infer_jpeg(encoded.tobytes(), now_sec=time.time())
        detections: list[Detection] = []
        for person in result.get("people", []):
            confidence = float(person.get("det_score", 0.0))
            if confidence < self._confidence:
                continue
            keypoints = tuple(
                PoseKeypoint(
                    x=float(kp.get("x", 0.0)),
                    y=float(kp.get("y", 0.0)),
                    confidence=float(kp.get("conf", 0.0)),
                )
                for kp in person.get("keypoints", [])
            )
            bbox = tuple(float(v) for v in person.get("bbox", (0.0, 0.0, 0.0, 0.0)))
            track_id = person.get("track_id")
            detections.append(
                Detection(
                    bbox=bbox,
                    confidence=confidence,
                    class_id=0,
                    label="person",
                    track_id=int(track_id) if track_id is not None else None,
                    keypoints=keypoints,
                    stable_score=float(person.get("stable_score", 0.0)),
                    age=int(person.get("age", 0)),
                    missed=int(person.get("missed", 0)),
                )
            )
        return detections
