from person_tasks.intent import parse_person_task_intent


def test_follow_me_phrases_parse_to_nearest_follow():
    intent = parse_person_task_intent("跟着我")

    assert intent == {
        "tool": "control_person_follow",
        "args": {"action": "follow", "target": "nearest"},
    }


def test_follow_role_phrase_parse_to_identity_follow():
    intent = parse_person_task_intent("请跟着角色A")

    assert intent == {
        "tool": "control_person_follow",
        "args": {"action": "follow", "target": "A"},
    }


def test_seek_role_phrase_parse_to_identity_seek():
    intent = parse_person_task_intent("找一下角色B在哪里")

    assert intent == {
        "tool": "control_person_follow",
        "args": {"action": "seek", "target": "B"},
    }


def test_go_to_role_side_phrases_parse_to_identity_seek():
    assert parse_person_task_intent("到角色A身边来") == {
        "tool": "control_person_follow",
        "args": {"action": "seek", "target": "A"},
    }
    assert parse_person_task_intent("到A身边来") == {
        "tool": "control_person_follow",
        "args": {"action": "seek", "target": "A"},
    }
    assert parse_person_task_intent("找A") == {
        "tool": "control_person_follow",
        "args": {"action": "seek", "target": "A"},
    }


def test_stop_follow_phrase_parse_to_stop():
    for text in ("不要跟了", "退出跟随", "退出跟随模式", "关闭跟随模式"):
        assert parse_person_task_intent(text) == {
            "tool": "control_person_follow",
            "args": {"action": "stop", "target": "nearest"},
        }


def test_negated_follow_phrases_parse_to_stop_before_follow():
    for text in ("别跟随我了", "不要跟随我", "不用跟随我", "别跟着我了", "不要跟着我", "别跟着了"):
        assert parse_person_task_intent(text) == {
            "tool": "control_person_follow",
            "args": {"action": "stop", "target": "nearest"},
        }


def test_stop_seek_phrases_parse_to_stop_before_seek():
    for text in ("停止寻找", "停止找人", "不要找了", "别找了"):
        assert parse_person_task_intent(text) == {
            "tool": "control_person_follow",
            "args": {"action": "stop", "target": "nearest"},
        }


def test_identity_question_parse_to_observe_tool():
    intent = parse_person_task_intent("你知道我是谁吗")

    assert intent == {
        "tool": "observe_people_identity",
        "args": {},
    }
