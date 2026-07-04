import json

from person_tasks.tools import execute_person_tool


class FakeController:
    def __init__(self):
        self.calls = []

    def control(self, action, target):
        self.calls.append(("control", action, target))
        return {"ok": True, "action": action, "target": target, "target_name": "tao"}

    def observe_people(self):
        self.calls.append(("observe",))
        return {
            "ok": True,
            "visible_people": [
                {"track_id": 1, "known": True, "name": "tao", "position": "center"},
                {"track_id": 2, "known": False, "name": None, "position": "left"},
            ],
        }


def test_control_person_follow_tool_resolves_role_before_calling_controller():
    controller = FakeController()

    result = execute_person_tool(
        "control_person_follow",
        json.dumps({"action": "follow", "target": "A"}),
        controller=controller,
    )

    assert controller.calls == [("control", "follow", "tao")]
    payload = json.loads(result[0]["text"])
    assert payload["ok"] is True
    assert payload["target_name"] == "tao"


def test_observe_people_identity_tool_returns_structured_json_for_llm():
    controller = FakeController()

    result = execute_person_tool("observe_people_identity", "{}", controller=controller)

    assert controller.calls == [("observe",)]
    payload = json.loads(result[0]["text"])
    assert payload["ok"] is True
    assert payload["visible_people"][0]["name"] == "tao"
    assert payload["visible_people"][1]["known"] is False
