import numpy as np
import pytest

from depth_camera_perception.face_identity_adapter import FaceIdentityAdapter


class FakeRegistry:
    def __init__(self):
        self.people = {
            "tao": {"person_id": "tao", "display_name": "tao", "enabled": True},
            "xiao": {"person_id": "xiao", "display_name": "xiao", "enabled": True},
        }
        self.embeddings = [
            {"person_id": "tao"},
            {"person_id": "tao"},
            {"person_id": "xiao"},
        ]

    def find_person_id(self, name):
        return name if name in self.people else None

    def match(self, embedding):
        if float(embedding[0]) > 0.5:
            return "tao", "tao", 0.81, {"reason": "matched", "margin": 0.2}
        return None, "unknown", 0.1, {"reason": "below_threshold"}


class FakeNative:
    def infer_jpeg(self, jpeg_bytes, include_embedding=True):
        return {
            "ok": True,
            "faces": [
                {
                    "bbox": [10.0, 20.0, 60.0, 90.0],
                    "det_score": 0.92,
                    "quality": 0.77,
                    "embedding": np.ones((512,), dtype=np.float32),
                }
            ],
        }


def test_load_target_identity_from_registry():
    adapter = FaceIdentityAdapter(registry=FakeRegistry(), native=FakeNative())

    target = adapter.load_target_identity("tao")

    assert target.person_id == "tao"
    assert target.display_name == "tao"
    assert target.embedding_count == 2


def test_load_target_identity_raises_for_unknown_name():
    adapter = FaceIdentityAdapter(registry=FakeRegistry(), native=FakeNative())

    with pytest.raises(ValueError, match="identity not found"):
        adapter.load_target_identity("missing")


def test_infer_faces_matches_registry_identity():
    adapter = FaceIdentityAdapter(registry=FakeRegistry(), native=FakeNative())
    frame = np.zeros((32, 32, 3), dtype=np.uint8)

    faces = adapter.infer_faces_bgr(frame)

    assert len(faces) == 1
    assert faces[0].person_id == "tao"
    assert faces[0].display_name == "tao"
    assert faces[0].identity_score == 0.81
