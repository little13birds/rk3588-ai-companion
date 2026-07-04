from person_tasks.roles import resolve_person_target


def test_role_aliases_resolve_to_identity_names():
    assert resolve_person_target("A") == "tao"
    assert resolve_person_target("角色A") == "tao"
    assert resolve_person_target("tao") == "tao"
    assert resolve_person_target("B") == "xiao"
    assert resolve_person_target("角色B") == "xiao"
    assert resolve_person_target("xiao") == "xiao"


def test_follow_me_resolves_to_nearest_target():
    assert resolve_person_target("我") == "nearest"
    assert resolve_person_target("nearest") == "nearest"
