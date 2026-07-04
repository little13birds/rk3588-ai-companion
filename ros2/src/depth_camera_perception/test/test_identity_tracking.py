import numpy as np

from depth_camera_perception.identity_tracking import (
    FaceObservation,
    PosePersonObservation,
    associate_faces_to_people,
)


def make_person(track_id=7, bbox=(100.0, 80.0, 300.0, 420.0), distance_m=1.4):
    return PosePersonObservation(
        track_id=track_id,
        bbox=bbox,
        confidence=0.91,
        distance_m=distance_m,
        keypoints=(),
        stable_score=0.8,
    )


def make_face(bbox=(150.0, 110.0, 230.0, 210.0), person_id="tao", score=0.75):
    return FaceObservation(
        bbox=bbox,
        det_score=0.9,
        quality=0.8,
        embedding=np.ones((512,), dtype=np.float32),
        person_id=person_id,
        display_name=person_id,
        identity_score=score,
        match_detail={"reason": "matched", "margin": 0.2},
    )


def test_associate_face_to_person_when_face_center_is_inside_person_bbox():
    people = [make_person(track_id=3)]
    faces = [make_face()]

    observations = associate_faces_to_people(people, faces)

    assert len(observations) == 1
    observation = observations[0]
    assert observation.person.track_id == 3
    assert observation.face is faces[0]
    assert observation.association_score >= 0.2


def test_do_not_associate_face_outside_person_bbox():
    people = [make_person(track_id=3)]
    faces = [make_face(bbox=(400.0, 10.0, 460.0, 80.0))]

    observations = associate_faces_to_people(people, faces)

    assert len(observations) == 1
    assert observations[0].face is None
    assert observations[0].association_score == 0.0


from depth_camera_perception.identity_tracking import IdentityTargetTracker, TargetIdentity


def make_identity(name="tao"):
    return TargetIdentity(person_id=name, display_name=name, embedding_count=2)


def test_identity_tracker_binds_matching_face_track_id():
    tracker = IdentityTargetTracker(make_identity())
    observations = associate_faces_to_people([make_person(track_id=9)], [make_face(person_id="tao")])

    selection = tracker.update(observations, now_s=1.0, obstacle_active=False)

    assert selection.target is observations[0]
    assert selection.state == "TRACKING_ID"
    assert selection.reason == "target_identity_bound"
    assert selection.bound_track_id == 9


def test_identity_tracker_ignores_non_target_identity():
    tracker = IdentityTargetTracker(make_identity("tao"))
    observations = associate_faces_to_people([make_person(track_id=9)], [make_face(person_id="xiao")])

    selection = tracker.update(observations, now_s=1.0, obstacle_active=False)

    assert selection.target is None
    assert selection.state == "SEARCH_IDENTITY"
    assert selection.reason == "target_identity_not_visible"


def test_identity_tracker_keeps_bound_track_for_short_face_loss():
    tracker = IdentityTargetTracker(make_identity(), identity_lost_timeout_s=0.5)
    matched = associate_faces_to_people([make_person(track_id=9)], [make_face(person_id="tao")])
    tracker.update(matched, now_s=1.0, obstacle_active=False)
    no_face = associate_faces_to_people([make_person(track_id=9)], [])

    selection = tracker.update(no_face, now_s=1.2, obstacle_active=False)

    assert selection.target is no_face[0]
    assert selection.state == "TRACKING_ID"
    assert selection.reason == "tracking_bound_id_without_face"


def test_identity_tracker_keeps_visible_bound_track_after_face_timeout():
    tracker = IdentityTargetTracker(make_identity(), identity_lost_timeout_s=0.5)
    matched = associate_faces_to_people([make_person(track_id=9)], [make_face(person_id="tao")])
    tracker.update(matched, now_s=1.0, obstacle_active=False)
    no_face = associate_faces_to_people([make_person(track_id=9)], [])

    selection = tracker.update(no_face, now_s=2.0, obstacle_active=False)

    assert selection.target is no_face[0]
    assert selection.state == "TRACKING_ID"
    assert selection.reason == "tracking_bound_id_without_face"
    assert selection.bound_track_id == 9


def test_identity_tracker_returns_to_search_after_non_obstacle_loss_timeout():
    tracker = IdentityTargetTracker(make_identity(), identity_lost_timeout_s=0.5)
    matched = associate_faces_to_people([make_person(track_id=9)], [make_face(person_id="tao")])
    tracker.update(matched, now_s=1.0, obstacle_active=False)

    selection = tracker.update([], now_s=1.6, obstacle_active=False)

    assert selection.target is None
    assert selection.state == "SEARCH_IDENTITY"
    assert selection.reason == "identity_lost_outside_obstacle"
    assert selection.bound_track_id is None


def test_identity_tracker_marks_temporary_loss_during_obstacle():
    tracker = IdentityTargetTracker(make_identity(), identity_lost_timeout_s=0.5)
    matched = associate_faces_to_people([make_person(track_id=9)], [make_face(person_id="tao")])
    tracker.update(matched, now_s=1.0, obstacle_active=False)

    selection = tracker.update([], now_s=1.6, obstacle_active=True)

    assert selection.target is None
    assert selection.state == "TEMP_LOST_DURING_OBSTACLE"
    assert selection.reason == "identity_lost_during_obstacle"
    assert selection.temporary_lost_due_to_obstacle is True


from depth_camera_perception.identity_tracking import detections_to_pose_people
from depth_camera_perception.person_detector import Detection


def test_detections_to_pose_people_keeps_distance_and_track_id():
    detection = Detection(
        bbox=(10.0, 20.0, 100.0, 220.0),
        confidence=0.93,
        class_id=0,
        label="person",
        track_id=8,
        distance_m=1.55,
        stable_score=0.6,
    )

    people = detections_to_pose_people([detection])

    assert len(people) == 1
    assert people[0].track_id == 8
    assert people[0].distance_m == 1.55
    assert people[0].bbox == detection.bbox
