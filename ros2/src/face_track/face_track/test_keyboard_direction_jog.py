"""Tests for keyboard direction jog helpers."""

from keyboard_direction_jog import DirectionJogCommand, apply_key
from servo_controller import apply_direction_jog


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

JOINT_LIMITS = [
    (-1.047, 1.047),
    (-1.047, 0.0),
    (0.524, 2.618),
    (-1.047, 1.047),
]


def test_six_keys_publish_left_right_front_back_up_down_signs():
    command = DirectionJogCommand()

    assert apply_key(command, "a")
    assert command.direction == [-1.0, 0.0, 0.0]
    assert apply_key(command, "d")
    assert command.direction == [1.0, 0.0, 0.0]

    assert apply_key(command, "w")
    assert command.direction == [0.0, 1.0, 0.0]
    assert apply_key(command, "s")
    assert command.direction == [0.0, -1.0, 0.0]

    assert apply_key(command, "q")
    assert command.direction == [0.0, 0.0, -1.0]
    assert apply_key(command, "e")
    assert command.direction == [0.0, 0.0, 1.0]
    print("test_six_keys_publish_left_right_front_back_up_down_signs PASS")


def test_unknown_key_clears_direction_without_exit():
    command = DirectionJogCommand()

    assert apply_key(command, "d")
    assert command.direction == [1.0, 0.0, 0.0]
    assert apply_key(command, "z")
    assert command.direction == [0.0, 0.0, 0.0]
    print("test_unknown_key_clears_direction_without_exit PASS")


def test_ctrl_c_requests_exit():
    command = DirectionJogCommand()

    assert not apply_key(command, "\x03")
    print("test_ctrl_c_requests_exit PASS")


def test_direction_jog_maps_axes_through_visual_servo_logic():
    current = [0.0, -0.5, 1.0, 0.0]

    assert apply_direction_jog(
        current=current,
        direction=[1.0, 0.0, 0.0],
        error_step=0.2,
        gains=SERVO_GAINS,
        limits=JOINT_LIMITS,
    ) == [0.025, -0.5, 1.0, 0.0]

    assert apply_direction_jog(
        current=current,
        direction=[0.0, 1.0, 0.0],
        error_step=0.2,
        gains=SERVO_GAINS,
        limits=JOINT_LIMITS,
    ) == [0.0, -0.52, 0.974, 0.0]

    assert apply_direction_jog(
        current=current,
        direction=[0.0, 0.0, -1.0],
        error_step=0.2,
        gains=SERVO_GAINS,
        limits=JOINT_LIMITS,
    ) == [0.0, -0.5, 1.0, 0.026]
    print("test_direction_jog_maps_axes_through_visual_servo_logic PASS")


def test_direction_jog_blocks_distance_pair_when_either_joint_hits_limit():
    current = [0.0, -0.005, 1.3, 0.0]

    assert apply_direction_jog(
        current=current,
        direction=[0.0, -1.0, 0.0],
        error_step=0.2,
        gains=SERVO_GAINS,
        limits=JOINT_LIMITS,
    ) == current
    print("test_direction_jog_blocks_distance_pair_when_either_joint_hits_limit PASS")


if __name__ == "__main__":
    test_six_keys_publish_left_right_front_back_up_down_signs()
    test_unknown_key_clears_direction_without_exit()
    test_ctrl_c_requests_exit()
    test_direction_jog_maps_axes_through_visual_servo_logic()
    test_direction_jog_blocks_distance_pair_when_either_joint_hits_limit()
    print("ALL PASS")
