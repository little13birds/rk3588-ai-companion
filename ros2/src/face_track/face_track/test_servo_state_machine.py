"""Pure-logic tests for the staged reading-arm servo state machine."""

from pathlib import Path

from servo_controller import (
    CoarseAlignmentGate,
    LostBookGate,
    NextPageLocalSearch,
    STATE_COARSE_ALIGN,
    STATE_EXIT_RETURN_HOME,
    STATE_FINE_ALIGN,
    STATE_IDLE,
    STATE_NEXT_PAGE_FINE_ALIGN,
    STATE_NEXT_PAGE_LOCAL_SEARCH,
    STATE_READY,
    STATE_STARTUP_SEARCH,
    StartupBookSearch,
    StableDetectionGate,
    alignment_state_for_found_book,
    apply_coarse_alignment,
    apply_fine_alignment,
    classify_visual_freshness,
    control_publish_period,
    physical_j3_search_levels,
    recovery_state_for_confirmed_loss,
    return_home_pose,
    scale_joint_deltas,
    should_publish_hold_command,
    valid_book_detection,
)


def _servo_config_value(name):
    config_path = Path(__file__).resolve().parents[1] / "config" / "servo_params.yaml"
    prefix = f"{name}:"
    for line in config_path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            raw = stripped.split(":", 1)[1].split("#", 1)[0].strip()
            return float(raw)
    raise AssertionError(f"missing servo config value: {name}")


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

FINE_GAINS = {
    "kp_base": 0.08,
    "kd_base": 0.03,
    "max_delta_base": 0.012,
    "deadband_x": 0.16,
}

LIMITS = [
    (-1.047, 1.047),
    (-1.047, 0.0),
    (0.524, 2.618),
    (-1.047, 1.047),
]


def test_startup_search_visits_three_j3_levels_while_holding_j2_j4():
    search = StartupBookSearch(
        j3_levels=physical_j3_search_levels(
            j3_min=0.524,
            search_j3_mid=1.571,
            j3_max=2.618,
        ),
        search_min=-0.1,
        search_max=0.1,
        search_step=0.1,
        max_delta=[0.2, 0.2, 0.6, 0.2],
        limits=LIMITS,
    )
    position = [0.0, 0.0, 1.127, 0.0]
    history = []

    done = False
    for _ in range(40):
        position, done = search.advance(position)
        history.append(position)
        assert position[1] == 0.0
        assert position[3] == 0.0
        if done:
            break

    assert done
    assert any(abs(pos[2] - 2.618) < 0.001 and abs(pos[0] - 0.1) < 0.001 for pos in history)
    assert any(abs(pos[2] - 1.571) < 0.001 and abs(pos[0] - 0.1) < 0.001 for pos in history)
    assert any(abs(pos[2] - 0.524) < 0.001 and abs(pos[0] - 0.1) < 0.001 for pos in history)
    print("test_startup_search_visits_three_j3_levels_while_holding_j2_j4 PASS")


def test_coarse_alignment_moves_only_j1_j3_and_holds_j2_j4_zero():
    next_pos, deltas = apply_coarse_alignment(
        current=[0.1, -0.4, 1.0, 0.3],
        e_x=0.2,
        e_y=0.2,
        de_x=0.0,
        de_y=0.0,
        gains=SERVO_GAINS,
        limits=LIMITS,
    )

    assert next_pos[0] > 0.1
    assert next_pos[1] == 0.0
    assert next_pos[2] > 1.0
    assert next_pos[3] == 0.0
    assert deltas[1] == 0.0
    assert deltas[3] == 0.0
    print("test_coarse_alignment_moves_only_j1_j3_and_holds_j2_j4_zero PASS")


def test_coarse_gate_requires_configured_stable_time():
    gate = CoarseAlignmentGate(threshold_x=0.12, threshold_y=0.12, stable_sec=0.3)

    assert not gate.update(e_x=0.10, e_y=0.10, dt=0.1)
    assert not gate.update(e_x=0.10, e_y=0.10, dt=0.1)
    assert gate.update(e_x=0.10, e_y=0.10, dt=0.1)
    assert not gate.update(e_x=0.13, e_y=0.10, dt=0.1)
    print("test_coarse_gate_requires_configured_stable_time PASS")


def test_fine_alignment_blocks_both_distance_joints_when_either_hits_limit():
    next_pos, deltas = apply_fine_alignment(
        current=[0.0, -0.005, 1.3, 0.0],
        e_x=0.0,
        e_y=0.0,
        e_r=0.2,
        de_x=0.0,
        de_y=0.0,
        de_r=0.0,
        gains=SERVO_GAINS,
        fine_base_gains=FINE_GAINS,
        limits=LIMITS,
    )

    assert next_pos[1] == -0.005
    assert next_pos[2] == 1.3
    assert deltas[1] == 0.0
    assert deltas[2] == 0.0
    print("test_fine_alignment_blocks_both_distance_joints_when_either_hits_limit PASS")


def test_fine_alignment_can_invert_wrist_vertical_error_sign():
    gains = dict(SERVO_GAINS)
    gains["fine_wrist_error_sign"] = -1.0

    next_pos, deltas = apply_fine_alignment(
        current=[0.0, -0.4, 1.3, 0.0],
        e_x=0.0,
        e_y=0.2,
        e_r=0.0,
        de_x=0.0,
        de_y=0.0,
        de_r=0.0,
        gains=gains,
        fine_base_gains=FINE_GAINS,
        limits=LIMITS,
    )

    assert next_pos[0] == 0.0
    assert next_pos[1] == -0.4
    assert next_pos[2] == 1.3
    assert deltas[3] < 0.0
    assert next_pos[3] < 0.0
    print("test_fine_alignment_can_invert_wrist_vertical_error_sign PASS")


def test_next_page_local_search_moves_only_j1_j4_from_preserved_pose():
    center = [0.1, -0.3, 1.4, 0.2]
    search = NextPageLocalSearch(
        center=center,
        radii=[0.524, 0.785],
        step=0.2,
        limits=LIMITS,
    )

    next_pos, done = search.advance(center)

    assert not done
    assert next_pos[0] > center[0]
    assert next_pos[1] == center[1]
    assert next_pos[2] == center[2]
    assert next_pos[3] == center[3]
    print("test_next_page_local_search_moves_only_j1_j4_from_preserved_pose PASS")


def test_startup_search_config_uses_plus_minus_45_j3_levels():
    levels = physical_j3_search_levels(
        j3_min=_servo_config_value("search_j3_min"),
        search_j3_mid=_servo_config_value("search_j3_mid"),
        j3_max=_servo_config_value("search_j3_max"),
    )

    assert levels == [2.356, 1.571, 0.785]
    print("test_startup_search_config_uses_plus_minus_45_j3_levels PASS")


def test_j3_physical_lower_is_numeric_max_limit():
    assert physical_j3_search_levels(
        j3_min=0.524,
        search_j3_mid=1.571,
        j3_max=2.618,
    ) == [2.618, 1.571, 0.524]
    assert return_home_pose(j3_physical_lower=2.618) == [0.0, 0.0, 2.618, 0.0]
    print("test_j3_physical_lower_is_numeric_max_limit PASS")


def test_lost_book_gate_requires_continuous_loss_before_triggering():
    gate = LostBookGate(grace_sec=0.5)

    assert not gate.update(found=False, dt=0.2)
    assert not gate.update(found=True, dt=0.1)
    assert not gate.update(found=False, dt=0.3)
    assert gate.update(found=False, dt=0.2)
    gate.reset()
    assert not gate.update(found=False, dt=0.4)
    print("test_lost_book_gate_requires_continuous_loss_before_triggering PASS")


def test_stable_detection_gate_requires_continuous_found_before_confirming():
    gate = StableDetectionGate(stable_sec=0.3)

    assert not gate.update(found=True, dt=0.1)
    assert not gate.update(found=True, dt=0.1)
    gate.reset()
    assert not gate.update(found=True, dt=0.2)
    assert not gate.update(found=False, dt=0.1)
    assert not gate.update(found=True, dt=0.2)
    assert gate.update(found=True, dt=0.1)
    assert gate.stable_time >= 0.3
    print("test_stable_detection_gate_requires_continuous_found_before_confirming PASS")


def test_confirmed_loss_uses_local_search_from_fine_states():
    assert recovery_state_for_confirmed_loss(STATE_FINE_ALIGN) == STATE_NEXT_PAGE_LOCAL_SEARCH
    assert recovery_state_for_confirmed_loss(STATE_NEXT_PAGE_FINE_ALIGN) == STATE_NEXT_PAGE_LOCAL_SEARCH
    assert recovery_state_for_confirmed_loss(STATE_COARSE_ALIGN) == STATE_STARTUP_SEARCH
    print("test_confirmed_loss_uses_local_search_from_fine_states PASS")


def test_ready_state_reenters_fine_align_when_vertical_error_returns():
    deadbands = (0.12, 0.12, 0.12)

    assert alignment_state_for_found_book(
        STATE_READY,
        raw_e_x=0.02,
        raw_e_y=0.13,
        raw_e_r=0.02,
        deadbands=deadbands,
    ) == STATE_FINE_ALIGN
    assert alignment_state_for_found_book(
        STATE_READY,
        raw_e_x=0.02,
        raw_e_y=0.08,
        raw_e_r=0.02,
        deadbands=deadbands,
    ) == STATE_READY
    assert alignment_state_for_found_book(
        STATE_NEXT_PAGE_FINE_ALIGN,
        raw_e_x=0.02,
        raw_e_y=0.13,
        raw_e_r=0.02,
        deadbands=deadbands,
    ) == STATE_NEXT_PAGE_FINE_ALIGN
    print("test_ready_state_reenters_fine_align_when_vertical_error_returns PASS")


def test_initial_pose_motion_can_be_speed_scaled_without_changing_servo_gains():
    base = [0.025, 0.02, 0.026, 0.026]

    assert scale_joint_deltas(base, 2.0) == [0.05, 0.04, 0.052, 0.052]
    assert base == [0.025, 0.02, 0.026, 0.026]
    print("test_initial_pose_motion_can_be_speed_scaled_without_changing_servo_gains PASS")


def test_hold_publish_policy_only_publishes_during_active_states():
    assert not should_publish_hold_command(
        STATE_IDLE,
        preparing=False,
        tracking=False,
        hold_enabled=True,
    )
    assert should_publish_hold_command(
        STATE_READY,
        preparing=False,
        tracking=True,
        hold_enabled=True,
    )
    assert should_publish_hold_command(
        STATE_EXIT_RETURN_HOME,
        preparing=False,
        tracking=False,
        hold_enabled=True,
    )
    assert should_publish_hold_command(
        STATE_IDLE,
        preparing=True,
        tracking=False,
        hold_enabled=True,
    )
    assert not should_publish_hold_command(
        STATE_READY,
        preparing=False,
        tracking=True,
        hold_enabled=False,
    )
    print("test_hold_publish_policy_only_publishes_during_active_states PASS")


def test_control_publish_period_requires_positive_frequency():
    assert abs(control_publish_period(20.0) - 0.05) < 1e-9
    try:
        control_publish_period(0.0)
        assert False, "expected ValueError"
    except ValueError:
        pass
    print("test_control_publish_period_requires_positive_frequency PASS")


def test_visual_freshness_classification_has_hold_window_before_lost():
    assert classify_visual_freshness(0.1, stale_hold_sec=0.3, lost_sec=0.8) == "fresh"
    assert classify_visual_freshness(0.5, stale_hold_sec=0.3, lost_sec=0.8) == "hold"
    assert classify_visual_freshness(1.0, stale_hold_sec=0.3, lost_sec=0.8) == "lost"
    try:
        classify_visual_freshness(0.1, stale_hold_sec=0.8, lost_sec=0.3)
        assert False, "expected ValueError"
    except ValueError:
        pass
    print("test_visual_freshness_classification_has_hold_window_before_lost PASS")


def test_tiny_book_detection_is_treated_as_not_found_during_search():
    assert not valid_book_detection(found=1.0, area_ratio=0.001, min_area_ratio=0.02)
    assert not valid_book_detection(found=0.0, area_ratio=0.3, min_area_ratio=0.02)
    assert valid_book_detection(found=1.0, area_ratio=0.147, min_area_ratio=0.02)
    print("test_tiny_book_detection_is_treated_as_not_found_during_search PASS")


if __name__ == "__main__":
    test_startup_search_visits_three_j3_levels_while_holding_j2_j4()
    test_coarse_alignment_moves_only_j1_j3_and_holds_j2_j4_zero()
    test_coarse_gate_requires_configured_stable_time()
    test_fine_alignment_blocks_both_distance_joints_when_either_hits_limit()
    test_fine_alignment_can_invert_wrist_vertical_error_sign()
    test_next_page_local_search_moves_only_j1_j4_from_preserved_pose()
    test_startup_search_config_uses_plus_minus_45_j3_levels()
    test_j3_physical_lower_is_numeric_max_limit()
    test_lost_book_gate_requires_continuous_loss_before_triggering()
    test_stable_detection_gate_requires_continuous_found_before_confirming()
    test_confirmed_loss_uses_local_search_from_fine_states()
    test_ready_state_reenters_fine_align_when_vertical_error_returns()
    test_initial_pose_motion_can_be_speed_scaled_without_changing_servo_gains()
    test_hold_publish_policy_only_publishes_during_active_states()
    test_control_publish_period_requires_positive_frequency()
    test_visual_freshness_classification_has_hold_window_before_lost()
    test_tiny_book_detection_is_treated_as_not_found_during_search()
    print("ALL PASS")
