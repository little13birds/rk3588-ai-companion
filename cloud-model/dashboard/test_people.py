"""Tests for dashboard person identity adapters."""

from dashboard.people import PersonIdentityClient
from dashboard.test_state import _make_state


class FakePeopleClient:
    def __init__(self):
        self.deleted = []
        self.enrolled = []

    def list_people(self):
        return {
            "ok": True,
            "people": [
                {"unique_name": "tao", "display_name": "tao", "embedding_count": 2},
                {"unique_name": "xiao", "display_name": "xiao", "embedding_count": 1},
            ],
        }

    def delete_person(self, unique_name):
        self.deleted.append(unique_name)
        return {"ok": True, "deleted": unique_name}

    def capture_candidates_from_jpeg(self, jpeg_bytes, source="upload"):
        assert jpeg_bytes == b"jpg-bytes"
        return {
            "ok": True,
            "known_faces": [{"unique_name": "tao", "display_name": "tao", "confidence": 0.78}],
            "candidates": [{
                "embedding": [0.01] * 512,
                "crop_jpeg_b64": "ZmFjZQ==",
                "quality": 0.83,
                "bbox": [1, 2, 3, 4],
                "source": source,
            }],
        }

    def enroll_face(self, payload):
        self.enrolled.append(dict(payload))
        return {"ok": True, "person_id": payload["unique_name"], "display_name": payload["unique_name"]}


class FakeSleepPresencePeopleClient:
    def __init__(self, known_faces):
        self.known_faces = known_faces
        self.calls = []

    def capture_candidates_from_jpeg(self, jpeg_bytes, source="upload"):
        self.calls.append((jpeg_bytes, source))
        return {
            "ok": True,
            "known_faces": list(self.known_faces),
            "candidates": [],
        }


class FakePersonTaskController:
    def __init__(self):
        self.calls = []

    def control(self, action, target):
        self.calls.append((action, target))
        return {"ok": True, "action": action, "target": target}


class FakeTrackerAdapter:
    def __init__(self):
        self.ensure_calls = 0

    def ensure_tracker_server(self):
        self.ensure_calls += 1


class FakeControllerWithAdapter:
    def __init__(self):
        self.adapter = FakeTrackerAdapter()


def test_person_identity_client_normalizes_registry_response():
    client = PersonIdentityClient(
        http_get=lambda path: {
            "ok": True,
            "people": [{"person_id": "tao", "display_name": "Tao"}],
            "embedding_counts": {"tao": 3},
            "total_embeddings": 3,
        }
    )

    data = client.list_people()

    assert data["ok"] is True
    assert data["people"][0]["unique_name"] == "tao"
    assert data["people"][0]["display_name"] == "Tao"
    assert data["people"][0]["embedding_count"] == 3


def test_person_identity_client_extracts_unknown_candidates_only():
    observe = {
        "ok": True,
        "people": [
            {
                "track_id": 1,
                "unique_name": "tao",
                "identity_confidence": 0.81,
                "face": {"display_name": "tao", "embedding": [0.1] * 512, "crop_jpeg_b64": "known"},
            },
            {
                "track_id": 2,
                "unique_name": None,
                "identity_confidence": 0.0,
                "face": {"embedding": [0.2] * 512, "crop_jpeg_b64": "unknown", "quality": 0.77},
            },
        ],
        "faces_unassigned": [
            {"embedding": [0.3] * 512, "crop_jpeg_b64": "free", "quality": 0.66}
        ],
    }
    client = PersonIdentityClient(http_post_jpeg=lambda path, jpeg: observe)

    data = client.capture_candidates_from_jpeg(b"jpg")

    assert [face["unique_name"] for face in data["known_faces"]] == ["tao"]
    assert len(data["candidates"]) == 2
    assert data["candidates"][0]["crop_jpeg_b64"] == "unknown"
    assert len(data["candidates"][0]["embedding"]) == 512


def test_dashboard_state_caches_candidate_embeddings_without_exposing_them():
    state, _safety, _reading = _make_state()
    client = FakePeopleClient()
    state.set_people_client(client)

    result = state.people_candidates_from_image(b"jpg-bytes", source="upload")

    assert result["ok"] is True
    assert result["known_faces"][0]["unique_name"] == "tao"
    candidate = result["candidates"][0]
    assert candidate["candidate_id"]
    assert "embedding" not in candidate

    enroll = state.enroll_person({"candidate_id": candidate["candidate_id"], "unique_name": "xiao"})

    assert enroll["ok"] is True
    assert client.enrolled[0]["unique_name"] == "xiao"
    assert client.enrolled[0]["embedding"] == [0.01] * 512
    assert client.enrolled[0]["crop_jpeg_b64"] == "ZmFjZQ=="


def test_dashboard_state_delete_person_and_camera_capture_errors():
    state, _safety, _reading = _make_state()
    client = FakePeopleClient()
    state.set_people_client(client)

    assert state.delete_person({"unique_name": "tao"})["ok"] is True
    assert client.deleted == ["tao"]

    capture = state.people_candidates_from_camera()
    assert capture["ok"] is False
    assert capture["error"] == "snapshot_unavailable"


def test_dashboard_state_ensures_tracker_server_before_people_registry():
    state, _safety, _reading = _make_state()
    controller = FakeControllerWithAdapter()
    state.set_person_task_controller(controller)
    state.set_people_client(FakePeopleClient())

    result = state.people_registry()

    assert result["ok"] is True
    assert controller.adapter.ensure_calls == 1


def test_dashboard_state_marks_person_task_done_after_arrival_event():
    state, _safety, _reading = _make_state()
    state.set_person_task_controller(FakePersonTaskController())
    state.request_person_seek({"target": "tao", "timeout_sec": 60})
    assert state.person_task_status()["active"] is True

    state.mark_person_task_done(reason="arrived", event={"target": "tao"})

    status = state.person_task_status()
    assert status["active"] is False
    assert status["stopped_reason"] == "arrived"


def test_dashboard_state_refreshes_sleep_presence_from_known_faces():
    state, _safety, _reading = _make_state()
    state.update_sleep_settings({"children": ["tao", "xiao"]})
    state.set_camera_snapshot_provider(lambda: b"platform-jpg")
    client = FakeSleepPresencePeopleClient([
        {"unique_name": "tao", "confidence": 0.82},
        {"unique_name": "adult", "confidence": 0.91},
    ])
    state.set_people_client(client)

    result = state.refresh_sleep_presence_from_identity()

    assert result["ok"] is True
    assert result["visible_children"] == ["tao"]
    assert client.calls == [(b"platform-jpg", "sleep_presence")]
    sleep = state.sleep_status()
    assert "tao" in sleep["visible_children"]
    assert "xiao" not in sleep["visible_children"]


if __name__ == "__main__":
    test_person_identity_client_normalizes_registry_response()
    test_person_identity_client_extracts_unknown_candidates_only()
    test_dashboard_state_caches_candidate_embeddings_without_exposing_them()
    test_dashboard_state_delete_person_and_camera_capture_errors()
    test_dashboard_state_ensures_tracker_server_before_people_registry()
    test_dashboard_state_marks_person_task_done_after_arrival_event()
    test_dashboard_state_refreshes_sleep_presence_from_known_faces()
    print("ALL PASS")
