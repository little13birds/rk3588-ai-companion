from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence, Tuple

import numpy as np

BBox = Tuple[float, float, float, float]


@dataclass(frozen=True)
class PoseKeypoint:
    x: float
    y: float
    confidence: float


@dataclass(frozen=True)
class PosePersonObservation:
    track_id: Optional[int]
    bbox: BBox
    confidence: float
    distance_m: Optional[float]
    keypoints: Sequence[PoseKeypoint] = ()
    stable_score: float = 0.0


@dataclass(frozen=True)
class FaceObservation:
    bbox: BBox
    det_score: float
    quality: float
    embedding: Optional[np.ndarray] = None
    person_id: Optional[str] = None
    display_name: Optional[str] = None
    identity_score: float = 0.0
    match_detail: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class IdentityObservation:
    person: PosePersonObservation
    face: Optional[FaceObservation]
    association_score: float = 0.0

    @property
    def track_id(self) -> Optional[int]:
        return self.person.track_id

    @property
    def distance_m(self) -> Optional[float]:
        return self.person.distance_m


@dataclass(frozen=True)
class TargetIdentity:
    person_id: str
    display_name: str
    embedding_count: int = 0


@dataclass(frozen=True)
class IdentityTargetSelection:
    state: str
    reason: str
    target: Optional[IdentityObservation] = None
    bound_track_id: Optional[int] = None
    target_person_id: Optional[str] = None
    target_display_name: Optional[str] = None
    identity_score: Optional[float] = None
    temporary_lost_due_to_obstacle: bool = False


class IdentityTargetTracker:
    def __init__(
        self,
        target_identity: TargetIdentity,
        *,
        identity_lost_timeout_s: float = 0.50,
    ):
        self.target_identity = target_identity
        self.identity_lost_timeout_s = float(identity_lost_timeout_s)
        self.bound_track_id: Optional[int] = None
        self._last_identity_s: Optional[float] = None
        self._temporary_lost_due_to_obstacle = False

    def update(
        self,
        observations: Sequence[IdentityObservation],
        *,
        now_s: float,
        obstacle_active: bool,
    ) -> IdentityTargetSelection:
        now_s = float(now_s)
        target_match = self._find_target_identity(observations)
        if target_match is not None:
            self.bound_track_id = target_match.track_id
            self._last_identity_s = now_s
            self._temporary_lost_due_to_obstacle = False
            return self._selection(
                "TRACKING_ID",
                "target_identity_bound",
                target_match,
                target_match.face.identity_score if target_match.face else None,
            )

        bound = self._find_bound_track(observations)
        if bound is not None:
            return self._selection("TRACKING_ID", "tracking_bound_id_without_face", bound, None)

        if obstacle_active and self.bound_track_id is not None:
            self._temporary_lost_due_to_obstacle = True
            return self._selection(
                "TEMP_LOST_DURING_OBSTACLE",
                "identity_lost_during_obstacle",
                None,
                None,
            )

        if self.bound_track_id is not None:
            self.bound_track_id = None
            self._last_identity_s = None
            return self._selection("SEARCH_IDENTITY", "identity_lost_outside_obstacle", None, None)

        return self._selection("SEARCH_IDENTITY", "target_identity_not_visible", None, None)

    def _find_target_identity(
        self,
        observations: Sequence[IdentityObservation],
    ) -> Optional[IdentityObservation]:
        for observation in observations:
            face = observation.face
            if face is None:
                continue
            if str(face.person_id or "").lower() == self.target_identity.person_id.lower():
                return observation
        return None

    def _find_bound_track(
        self,
        observations: Sequence[IdentityObservation],
    ) -> Optional[IdentityObservation]:
        if self.bound_track_id is None:
            return None
        for observation in observations:
            if observation.track_id == self.bound_track_id:
                return observation
        return None

    def _identity_lost_timed_out(self, now_s: float) -> bool:
        if self._last_identity_s is None:
            return True
        return now_s - self._last_identity_s >= self.identity_lost_timeout_s

    def _selection(
        self,
        state: str,
        reason: str,
        target: Optional[IdentityObservation],
        score: Optional[float],
    ) -> IdentityTargetSelection:
        return IdentityTargetSelection(
            state=state,
            reason=reason,
            target=target,
            bound_track_id=self.bound_track_id,
            target_person_id=self.target_identity.person_id,
            target_display_name=self.target_identity.display_name,
            identity_score=score,
            temporary_lost_due_to_obstacle=self._temporary_lost_due_to_obstacle,
        )


def associate_faces_to_people(
    people: Sequence[PosePersonObservation],
    faces: Sequence[FaceObservation],
    *,
    min_association_score: float = 0.2,
) -> list[IdentityObservation]:
    assignments: dict[int, tuple[FaceObservation, float]] = {}
    for face in faces:
        best_index = -1
        best_score = 0.0
        face_area = max(_bbox_area(face.bbox), 1.0)
        for index, person in enumerate(people):
            inter_ratio = _bbox_intersection(face.bbox, person.bbox) / face_area
            center_bonus = 0.2 if _bbox_center_in(face.bbox, person.bbox) else 0.0
            score = inter_ratio + center_bonus
            if score > best_score:
                best_index = index
                best_score = score
        if best_index >= 0 and best_score >= min_association_score:
            current = assignments.get(best_index)
            if current is None or best_score > current[1]:
                assignments[best_index] = (face, best_score)

    observations: list[IdentityObservation] = []
    for index, person in enumerate(people):
        assigned = assignments.get(index)
        if assigned is None:
            observations.append(IdentityObservation(person=person, face=None, association_score=0.0))
        else:
            face, score = assigned
            observations.append(IdentityObservation(person=person, face=face, association_score=score))
    return observations


def detections_to_pose_people(detections) -> list[PosePersonObservation]:
    people: list[PosePersonObservation] = []
    for detection in detections:
        keypoints = tuple(
            PoseKeypoint(
                x=float(kp.x),
                y=float(kp.y),
                confidence=float(kp.confidence),
            )
            for kp in getattr(detection, "keypoints", ())
        )
        people.append(
            PosePersonObservation(
                track_id=getattr(detection, "track_id", None),
                bbox=tuple(float(v) for v in detection.bbox),
                confidence=float(detection.confidence),
                distance_m=getattr(detection, "distance_m", None),
                keypoints=keypoints,
                stable_score=float(getattr(detection, "stable_score", 0.0)),
            )
        )
    return people


def _bbox_area(bbox: BBox) -> float:
    x1, y1, x2, y2 = bbox
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def _bbox_intersection(a: BBox, b: BBox) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    x1 = max(ax1, bx1)
    y1 = max(ay1, by1)
    x2 = min(ax2, bx2)
    y2 = min(ay2, by2)
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def _bbox_center_in(inner: BBox, outer: BBox) -> bool:
    cx = (inner[0] + inner[2]) * 0.5
    cy = (inner[1] + inner[3]) * 0.5
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]
