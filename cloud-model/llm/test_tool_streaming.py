"""Tool execution must transition directly to one streaming answer."""
import sys
import threading
from types import SimpleNamespace

sys.modules.setdefault(
    "openai",
    SimpleNamespace(
        OpenAI=lambda **_kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=None)
        )
    ),
)

from llm import chat


class FakeCompletions:
    def __init__(self, tool_names):
        self.tool_names = tool_names
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if kwargs.get("stream") is True:
            usage = SimpleNamespace(prompt_tokens=12, completion_tokens=3)
            delta = SimpleNamespace(content="朗读完成")
            return [
                SimpleNamespace(choices=[], usage=usage),
                SimpleNamespace(
                    choices=[SimpleNamespace(delta=delta)],
                    usage=None,
                ),
            ]

        if kwargs.get("stream") is False:
            tool_calls = [
                SimpleNamespace(
                    id=f"call-{index}",
                    function=SimpleNamespace(name=name, arguments="{}"),
                )
                for index, name in enumerate(self.tool_names, start=1)
            ]
            message = SimpleNamespace(content=None, tool_calls=tool_calls)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message)],
                usage=None,
            )
        raise AssertionError("unexpected completion request: %r" % kwargs)


def _tool_defs(tool_names):
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name in tool_names
    ]


def _run(tool_names, system_prompt="你是语音助手。", user_text="看看"):
    fake = FakeCompletions(tool_names)
    original_client = chat.client
    chat.client = SimpleNamespace(
        chat=SimpleNamespace(completions=fake)
    )
    conv = chat.Conversation(
        system_prompt=system_prompt,
        max_tokens=40,
        tools=_tool_defs(tool_names),
    )
    conv.messages.append({"role": "user", "content": user_text})
    executed = []

    def execute(name, args, **_kwargs):
        executed.append(name)
        return [{"type": "text", "text": f"{name}结果"}]

    conv._execute_tool = execute
    try:
        result = conv._call_api()
    finally:
        chat.client = original_client
    return fake, conv, executed, result


def test_tool_result_goes_directly_to_streaming_answer():
    fake, conv, executed, result = _run(["take_photo"])

    assert [request["stream"] for request in fake.requests] == [False, True]
    assert "tools" not in fake.requests[1]
    final_messages = fake.requests[1]["messages"]
    assert final_messages[-2]["role"] == "assistant"
    assert final_messages[-1]["role"] == "tool"
    assert final_messages[-1]["tool_call_id"] == "call-1"
    assert executed == ["take_photo"]
    assert result["text"] == "朗读完成"
    print("test_tool_result_goes_directly_to_streaming_answer PASS")


def test_all_tools_in_one_response_are_executed_before_streaming():
    fake, _conv, executed, result = _run(["get_brightness", "get_motion"])

    assert [request["stream"] for request in fake.requests] == [False, True]
    assert executed == ["get_brightness", "get_motion"]
    final_messages = fake.requests[1]["messages"]
    assert [message["role"] for message in final_messages[-3:]] == [
        "assistant",
        "tool",
        "tool",
    ]
    assert [message["tool_call_id"] for message in final_messages[-2:]] == [
        "call-1",
        "call-2",
    ]
    assert result["text"] == "朗读完成"
    print("test_all_tools_in_one_response_are_executed_before_streaming PASS")


def test_reading_context_uses_direct_take_photo_fast_path():
    fake, _conv, executed, result = _run(
        ["take_photo"],
        system_prompt="你是OCR朗读器。",
        user_text="读书",
    )

    assert [request["stream"] for request in fake.requests] == [True]
    final_messages = fake.requests[0]["messages"]
    assert final_messages[-1]["role"] == "user"
    assert final_messages[-1]["content"] == [{"type": "text", "text": "take_photo结果"}]
    assert executed == ["take_photo"]
    assert result["text"] == "朗读完成"
    print("test_reading_context_uses_direct_take_photo_fast_path PASS")


def test_reading_fast_path_cancel_after_photo_skips_streaming_answer():
    fake = FakeCompletions(["take_photo"])
    original_client = chat.client
    chat.client = SimpleNamespace(
        chat=SimpleNamespace(completions=fake)
    )
    conv = chat.Conversation(
        system_prompt="你是OCR朗读器。",
        max_tokens=40,
        tools=_tool_defs(["take_photo"]),
    )
    conv.messages.append({"role": "user", "content": "读书"})
    cancel_event = threading.Event()
    executed = []

    def execute(name, args, **kwargs):
        executed.append((name, kwargs.get("cancel_event") is cancel_event))
        cancel_event.set()
        return [{"type": "text", "text": "拍照被打断"}]

    conv._execute_tool = execute
    try:
        result = conv._call_api(cancel_event=cancel_event)
    finally:
        chat.client = original_client

    assert fake.requests == []
    assert executed == [("take_photo", True)]
    assert result["text"] == ""
    assert result["stats"]["chars"] == 0
    print("test_reading_fast_path_cancel_after_photo_skips_streaming_answer PASS")


if __name__ == "__main__":
    test_tool_result_goes_directly_to_streaming_answer()
    test_all_tools_in_one_response_are_executed_before_streaming()
    test_reading_context_uses_direct_take_photo_fast_path()
    test_reading_fast_path_cancel_after_photo_skips_streaming_answer()
    print("ALL PASS")
