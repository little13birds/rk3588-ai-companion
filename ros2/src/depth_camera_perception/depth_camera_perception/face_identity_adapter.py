from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

from .identity_tracking import FaceObservation, TargetIdentity


class FaceIdentityAdapter:
    def __init__(
        self,
        *,
        face_identity_root: str | Path = "/home/elf/face_identity_rk3588",
        registry=None,
        native=None,
        det_conf: float = 0.45,
        match_threshold: float = 0.42,
        margin_threshold: float = 0.04,
    ):
        self.face_identity_root = Path(face_identity_root).expanduser()
        if registry is not None and native is not None:
            self.registry = registry
            self.native = native
            return

        src_dir = self.face_identity_root / "src"
        if str(src_dir) not in sys.path:
            sys.path.insert(0, str(src_dir))

        from face_identity.native_runtime import FaceIdentityNative
        from face_identity.paths import (
            DEFAULT_NATIVE_LIB,
            DEFAULT_REGISTRY,
            DEFAULT_RKNN_DETECTOR,
            DEFAULT_RKNN_RECOGNIZER,
        )
        from face_identity.registry import FaceRegistry

        self.registry = registry or FaceRegistry(
            DEFAULT_REGISTRY,
            match_threshold=match_threshold,
            margin_threshold=margin_threshold,
        )
        self.native = native or FaceIdentityNative(
            DEFAULT_NATIVE_LIB,
            DEFAULT_RKNN_DETECTOR,
            DEFAULT_RKNN_RECOGNIZER,
            det_conf=det_conf,
        )

    def load_target_identity(self, name: str) -> TargetIdentity:
        person_id = self.registry.find_person_id(name)
        if not person_id:
            raise ValueError(f"identity not found: {name}")
        person = self.registry.people.get(person_id, {})
        count = sum(1 for item in self.registry.embeddings if item.get("person_id") == person_id)
        return TargetIdentity(
            person_id=person_id,
            display_name=str(person.get("display_name") or person_id),
            embedding_count=count,
        )

    def infer_faces_bgr(self, frame_bgr: np.ndarray) -> list[FaceObservation]:
        ok, encoded = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            return []
        result = self.native.infer_jpeg(encoded.tobytes(), include_embedding=True)
        faces: list[FaceObservation] = []
        for item in result.get("faces", []):
            embedding = item.get("embedding")
            person_id = None
            display_name = None
            score = 0.0
            detail = None
            if embedding is not None:
                person_id, display_name, score, detail = self.registry.match(embedding)
            bbox = tuple(float(v) for v in item.get("bbox", (0.0, 0.0, 0.0, 0.0)))
            faces.append(
                FaceObservation(
                    bbox=bbox,
                    det_score=float(item.get("det_score", 0.0)),
                    quality=float(item.get("quality", 0.0)),
                    embedding=embedding,
                    person_id=person_id,
                    display_name=display_name,
                    identity_score=float(score),
                    match_detail=detail,
                )
            )
        return faces
