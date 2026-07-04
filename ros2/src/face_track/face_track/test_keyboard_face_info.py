"""Tests for keyboard joint jog helpers."""

from keyboard_face_info import JointJogCommand, apply_key
from servo_controller import apply_joint_jog, compute_visual_servo_deltas


SERVO_GAINS = {
    "kp_base": 0.2,
    "kd_base": 0.10,
    "kp_shoulder": 0.15,
    "kd_shoulder": 0.0,
    "kp_elbow": 0.28,
    "kd_elbow": 0.15,
    "kp_wrist": 0.28,
    "kd_wrist": 0.15,
    "max_delta_base": 0.025,
    "max_delta_shoulder": 0.02,
    "max_delta_elbow": 0.026,
    "max_delta_wrist": 0.026,
}


def test_eight_keys_map_to_four_joint_deltas():
    command = JointJogCommand(step=0.1)

    assert apply_key(command, "q")
    assert command.deltas == [0.1, 0.0, 0.0, 0.0]
    assert apply_key(command, "a")
    assert command.deltas == [-0.1, 0.0, 0.0, 0.0]

    assert apply_key(command, "w")
    assert command.deltas == [0.0, 0.1, 0.0, 0.0]
    assert apply_key(command, "s")
    assert command.deltas == [0.0, -0.1, 0.0, 0.0]

    assert apply_key(command, "e")
    assert command.deltas == [0.0, 0.0, 0.1, 0.0]
    assert apply_key(command, "d")
    assert command.deltas == [0.0, 0.0, -0.1, 0.0]

    assert apply_key(command, "r")
    assert command.deltas == [0.0, 0.0, 0.0, 0.1]
    assert apply_key(command, "f")
    assert command.deltas == [0.0, 0.0, 0.0, -0.1]
    print("test_eight_keys_map_to_four_joint_deltas PASS")


def test_unknown_key_does_not_change_last_delta():
    command = JointJogCommand(step=0.1)

    assert apply_key(command, "q")
    assert command.deltas == [0.1, 0.0, 0.0, 0.0]
    assert apply_key(command, "z")
    assert command.deltas == [0.0, 0.0, 0.0, 0.0]
    print("test_unknown_key_does_not_change_last_delta PASS")


def test_ctrl_c_requests_exit():
    command = JointJogCommand(step=0.1)

    assert not apply_key(command, "\x03")
    print("test_ctrl_c_requests_exit PASS")


def test_servo_joint_jog_is_clamped_per_axis():
    current = [1.0, -1.5, 2.5, 1.0]
    deltas = [0.2, -0.2, 0.2, 0.2]
    limits = [(-1.047, 1.047), (-1.047, 0.0), (0.524, 2.618), (-1.047, 1.047)]

    assert apply_joint_jog(current, deltas, limits) == [1.047, -1.047, 2.618, 1.047]
    print("test_servo_joint_jog_is_clamped_per_axis PASS")


def test_vertical_error_moves_wrist_only():
    deltas = compute_visual_servo_deltas(
        e_x=0.0,
        e_y=0.2,
        e_r=0.0,
        de_x=0.0,
        de_y=0.0,
        de_r=0.0,
        gains=SERVO_GAINS,
    )

    assert deltas == [0.0, 0.0, 0.0, 0.026]
    print("test_vertical_error_moves_wrist_only PASS")


def test_distance_error_moves_shoulder_and_elbow_only():
    deltas = compute_visual_servo_deltas(
        e_x=0.0,
        e_y=0.0,
        e_r=0.2,
        de_x=0.0,
        de_y=0.0,
        de_r=0.0,
        gains=SERVO_GAINS,
    )

    assert deltas == [0.0, 0.02, 0.026, 0.0]
    print("test_distance_error_moves_shoulder_and_elbow_only PASS")


if __name__ == "__main__":
    test_eight_keys_map_to_four_joint_deltas()
    test_unknown_key_does_not_change_last_delta()
    test_ctrl_c_requests_exit()
    test_servo_joint_jog_is_clamped_per_axis()
    test_vertical_error_moves_wrist_only()
    test_distance_error_moves_shoulder_and_elbow_only()
    print("ALL PASS")
