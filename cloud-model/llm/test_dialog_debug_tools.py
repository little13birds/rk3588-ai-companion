import json

from llm.dialog_debug_tools import DialogDebugToolExecutor


def _payload(result):
    assert result and result[0]["type"] == "text"
    return json.loads(result[0]["text"])


def test_dialog_debug_tool_executor_returns_unavailable_for_sensors():
    executor = DialogDebugToolExecutor()

    payload = _payload(executor("get_temperature", "{}"))

    assert payload["ok"] is False
    assert payload["available"] is False
    assert payload["tool"] == "get_temperature"


def test_dialog_debug_tool_executor_returns_success_for_person_control():
    executor = DialogDebugToolExecutor()

    payload = _payload(executor("control_person_follow", '{"action":"follow","target":"A"}'))

    assert payload["ok"] is True
    assert payload["simulated"] is True
    assert payload["action"] == "follow"
    assert payload["target"] == "A"


def test_dialog_debug_tool_executor_returns_empty_people_list():
    executor = DialogDebugToolExecutor()

    payload = _payload(executor("observe_people_identity", "{}"))

    assert payload["ok"] is True
    assert payload["available"] is False
    assert payload["visible_people"] == []
