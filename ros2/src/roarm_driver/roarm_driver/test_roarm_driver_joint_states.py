import math

from roarm_driver import build_joint_command


def test_ignores_non_arm_joint_states():
    assert build_joint_command(
        ["front_right_joint", "front_left_joint", "back_right_joint", "back_left_joint"],
        [0.0, 0.0, 0.0, 0.0],
    ) is None
    print("test_ignores_non_arm_joint_states PASS")


def test_builds_arm_joint_command():
    command = build_joint_command(
        ["base_link_to_link1", "link1_to_link2", "link2_to_link3", "link3_to_gripper_link"],
        [0.1, -0.2, 1.3, 0.4],
    )

    assert command["T"] == 102
    assert command["base"] == -0.1
    assert command["shoulder"] == 0.2
    assert command["elbow"] == 1.3
    assert math.isclose(command["hand"], math.pi - 0.4, rel_tol=0.0, abs_tol=1e-6)
    print("test_builds_arm_joint_command PASS")


if __name__ == "__main__":
    test_ignores_non_arm_joint_states()
    test_builds_arm_joint_command()
    print("ALL PASS")
