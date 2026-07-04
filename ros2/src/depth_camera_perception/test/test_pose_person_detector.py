import numpy as np

from depth_camera_perception.pose_person_detector import PoseRknnPersonDetector


class FakePoseNative:
    def infer_jpeg(self, jpeg_bytes, now_sec=None):
        assert jpeg_bytes.startswith(b"\xff\xd8")
        return {
            "ok": True,
            "people": [
                {
                    "track_id": 11,
                    "bbox": [100.0, 120.0, 260.0, 420.0],
                    "det_score": 0.88,
                    "stable_score": 0.7,
                    "age": 3,
                    "missed": 0,
                    "keypoints": [
                        {"x": 120.0, "y": 130.0, "conf": 0.9},
                    ],
                }
            ],
        }


def test_pose_detector_returns_detection_with_track_and_keypoints():
    detector = PoseRknnPersonDetector(native=FakePoseNative(), confidence=0.4)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    detections = detector.detect(frame)

    assert len(detections) == 1
    detection = detections[0]
    assert detection.track_id == 11
    assert detection.bbox == (100.0, 120.0, 260.0, 420.0)
    assert detection.confidence == 0.88
    assert detection.label == "person"
    assert detection.keypoints[0].x == 120.0


def test_pose_detector_filters_low_confidence_people():
    native = FakePoseNative()
    detector = PoseRknnPersonDetector(native=native, confidence=0.9)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    assert detector.detect(frame) == []
