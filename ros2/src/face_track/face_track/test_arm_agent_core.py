"""arm_agent_core 单元测试。运行: python3 test_arm_agent_core.py（在 face_track 目录下）"""
from pathlib import Path

from arm_agent_core import (
    BaseSweepSearch,
    InitialPoseController,
    MotionSettleTracker,
    PrepareCommandRepublisher,
    reading_ready,
    select_camera_source,
)


def _load_servo_params():
    params = {}
    config = Path(__file__).resolve().parents[1] / "config" / "servo_params.yaml"
    for raw in config.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = [part.strip() for part in line.split(":", 1)]
        if not value:
            continue
        try:
            params[key] = float(value)
        except ValueError:
            pass
    return params


def test_settles_after_quiet_period():
    t = [0.0]
    trk = MotionSettleTracker(epsilon=0.005, settle_sec=1.0, time_fn=lambda: t[0])
    trk.update([0.0, 0.0, 1.127])
    assert not trk.settled()            # t=0，刚开始
    t[0] = 0.5
    trk.update([0.0, 0.0, 1.127])       # 未动
    assert not trk.settled()            # 仅 0.5s
    t[0] = 1.0
    assert trk.settled()                # 满 1.0s
    print("test_settles_after_quiet_period PASS")


def test_motion_resets_timer():
    t = [0.0]
    trk = MotionSettleTracker(epsilon=0.005, settle_sec=1.0, time_fn=lambda: t[0])
    trk.update([0.0, 0.0, 1.127])
    t[0] = 1.5
    trk.update([0.0, 0.0, 1.30])        # 肘部 Δ=0.173 > 0.005 → 运动
    assert not trk.settled()            # 计时重置
    t[0] = 2.4
    assert not trk.settled()            # 仅 0.9s
    t[0] = 2.5
    assert trk.settled()                # 满 1.0s
    print("test_motion_resets_timer PASS")


def test_small_jitter_under_epsilon_is_still():
    t = [0.0]
    trk = MotionSettleTracker(epsilon=0.005, settle_sec=1.0, time_fn=lambda: t[0])
    trk.update([0.0, 0.0, 1.127])
    t[0] = 0.5
    trk.update([0.001, 0.0, 1.129])     # 抖动 < epsilon → 不算动
    t[0] = 1.0
    assert trk.settled()
    print("test_small_jitter_under_epsilon_is_still PASS")


def test_reset_starts_a_new_settle_window():
    t = [0.0]
    trk = MotionSettleTracker(epsilon=0.005, settle_sec=1.0, time_fn=lambda: t[0])
    trk.update([0.0, 0.0, 1.127])
    t[0] = 2.0
    assert trk.settled()
    trk.reset()
    assert not trk.settled()
    t[0] = 3.5
    assert not trk.settled()             # 未收到新关节状态，不能误判静止
    trk.update([0.0, 0.0, 1.127])
    assert not trk.settled()
    t[0] = 4.5
    assert trk.settled()
    print("test_reset_starts_a_new_settle_window PASS")


def test_base_sweep_covers_both_sides_and_returns_to_start():
    sweep = BaseSweepSearch(min_pos=-1.0, max_pos=1.0, step=0.5)
    sweep.start(0.0)
    pos = 0.0
    seen = []
    done = False
    while not done:
        pos, done = sweep.advance(pos)
        seen.append(pos)
    assert max(seen) == 1.0
    assert min(seen) == -1.0
    assert pos == 0.0
    assert not sweep.active
    print("test_base_sweep_covers_both_sides_and_returns_to_start PASS")




def test_initial_pose_controller_steps_to_target_without_overshoot():
    ctl = InitialPoseController(
        target=[0.0, 0.0, 1.127, 0.0],
        max_delta=[0.1, 0.1, 0.1, 0.05],
        tolerance=0.001,
    )
    pos, done = ctl.advance([0.25, -0.2, 1.5, 0.08])
    assert pos == [0.15, -0.1, 1.4, 0.03]
    assert not done

    current = pos
    for _ in range(10):
        current, done = ctl.advance(current)
        if done:
            break
    assert done
    assert current == [0.0, 0.0, 1.127, 0.0]
    print("test_initial_pose_controller_steps_to_target_without_overshoot PASS")

def test_base_sweep_can_be_stopped_when_book_is_found():
    sweep = BaseSweepSearch(min_pos=-1.0, max_pos=1.0, step=0.2)
    sweep.start(0.0)
    pos, done = sweep.advance(0.0)
    assert pos == 0.2 and not done
    sweep.stop()
    same_pos, done = sweep.advance(pos)
    assert same_pos == pos and not done
    print("test_base_sweep_can_be_stopped_when_book_is_found PASS")


def test_reading_ready_rejects_incomplete_or_failed_search():
    assert reading_ready(True, True, True, False, False)
    assert not reading_ready(True, True, True, True, False)
    assert not reading_ready(True, True, True, False, True)
    assert not reading_ready(True, False, True, False, False)
    assert not reading_ready(False, True, True, False, False)
    assert not reading_ready(True, True, False, False, False)
    print("test_reading_ready_rejects_incomplete_or_failed_search PASS")


def test_prepare_command_republisher_retries_until_servo_ack():
    t = [10.0]
    retry = PrepareCommandRepublisher(interval_sec=0.15, time_fn=lambda: t[0])
    assert retry.should_publish(preparing=False, complete=False)
    assert not retry.should_publish(preparing=False, complete=False)
    t[0] = 10.14
    assert not retry.should_publish(preparing=False, complete=False)
    t[0] = 10.15
    assert retry.should_publish(preparing=False, complete=False)
    t[0] = 20.0
    assert not retry.should_publish(preparing=True, complete=False)
    assert not retry.should_publish(preparing=False, complete=True)
    print("test_prepare_command_republisher_retries_until_servo_ack PASS")


def test_stable_camera_path_is_preferred_over_numeric_index():
    stable_path = "/dev/v4l/by-id/usb-camera-video-index0"
    source = select_camera_source(
        stable_path, 21, exists_fn=lambda path: path == stable_path
    )
    assert source == stable_path
    assert select_camera_source(
        stable_path, 21, exists_fn=lambda _path: False
    ) == 21
    print("test_stable_camera_path_is_preferred_over_numeric_index PASS")


def test_strict_camera_path_rejects_numeric_fallback():
    stable_path = "/dev/v4l/by-id/usb-arm-camera-video-index0"
    try:
        select_camera_source(
            stable_path,
            21,
            exists_fn=lambda _path: False,
            allow_index_fallback=False,
        )
    except FileNotFoundError as exc:
        assert stable_path in str(exc)
    else:
        raise AssertionError("missing camera_device should not fall back in strict mode")
    print("test_strict_camera_path_rejects_numeric_fallback PASS")


def test_arm_camera_topology_path_is_stable_source():
    topology_path = "/dev/v4l/by-path/platform-fc880000.usb-usb-0:1.3:1.0-video-index0"
    assert select_camera_source(
        topology_path,
        21,
        exists_fn=lambda path: path == topology_path,
        allow_index_fallback=False,
    ) == topology_path
    print("test_arm_camera_topology_path_is_stable_source PASS")


def test_servo_safety_limits_match_reading_arm_constraints():
    params = _load_servo_params()
    assert params["j1_min"] == -1.047
    assert params["j1_max"] == 1.047
    assert params["search_min"] == params["j1_min"]
    assert params["search_max"] == params["j1_max"]
    assert params["j2_min"] == -1.047
    assert params["j2_max"] == 0.0
    assert params["j3_min"] == 0.524
    assert params["j3_max"] == 2.618
    assert params["j4_min"] == -1.047
    assert params["j4_max"] == 1.047
    print("test_servo_safety_limits_match_reading_arm_constraints PASS")


if __name__ == "__main__":
    test_settles_after_quiet_period()
    test_motion_resets_timer()
    test_small_jitter_under_epsilon_is_still()
    test_reset_starts_a_new_settle_window()
    test_base_sweep_covers_both_sides_and_returns_to_start()
    test_initial_pose_controller_steps_to_target_without_overshoot()
    test_base_sweep_can_be_stopped_when_book_is_found()
    test_reading_ready_rejects_incomplete_or_failed_search()
    test_prepare_command_republisher_retries_until_servo_ack()
    test_stable_camera_path_is_preferred_over_numeric_index()
    test_strict_camera_path_rejects_numeric_fallback()
    test_arm_camera_topology_path_is_stable_source()
    test_servo_safety_limits_match_reading_arm_constraints()
    print("ALL PASS")
