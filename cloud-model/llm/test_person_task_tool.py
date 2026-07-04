import json

from llm.chat import Conversation


class FakePersonTaskController:
    def control(self, action, target):
        return {"ok": True, "action": action, "target": target}

    def observe_people(self):
        return {"ok": True, "visible_people": [{"track_id": 1, "name": "tao"}]}


def test_conversation_executes_person_follow_tool_with_injected_controller():
    conv = Conversation(
        system_prompt="test",
        tools=[],
        person_task_controller=FakePersonTaskController(),
    )

    result = conv._execute_tool("control_person_follow", '{"action":"follow","target":"A"}')

    payload = json.loads(result[0]["text"])
    assert payload["ok"] is True
    assert payload["target"] == "tao"


def test_conversation_executes_people_observation_tool_with_injected_controller():
    conv = Conversation(
        system_prompt="test",
        tools=[],
        person_task_controller=FakePersonTaskController(),
    )

    result = conv._execute_tool("observe_people_identity", "{}")

    payload = json.loads(result[0]["text"])
    assert payload["visible_people"][0]["name"] == "tao"
