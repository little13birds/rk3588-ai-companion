from person_tasks.ros_adapter import RosPersonTaskAdapter


class FakeRunner:
    def __init__(self):
        self.commands = []
        self.kwargs = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        self.kwargs.append(kwargs)
        class Result:
            returncode = 0
            stdout = ""
        return Result()


def test_follow_identity_starts_support_stack_and_person_follow_through_cmd_vel_raw():
    runner = FakeRunner()
    adapter = RosPersonTaskAdapter(runner=runner, process_checker=lambda _pattern: False)

    result = adapter.control("follow", "tao")

    joined = "\n".join(runner.commands)
    assert result["ok"] is True
    assert "Mcnamu_driver_X3" in joined
    assert "base_node_X3" in joined
    assert "pub_odom_tf:=false" in joined
    assert "ros2 run depth_camera_perception fused_pose_monitor --ros-args" in joined
    assert "-p web_port:=8091" in joined
    assert "-p publish_period_s:=0.02" in joined
    assert "-p yaw_rate_scale:=1.53" in joined
    assert "-p linear_x_scale:=0.9" in joined
    assert "obstacle_guard.launch.py" in joined
    assert "input_cmd_vel_topic:=/cmd_vel_raw" in joined
    assert "output_cmd_vel_topic:=/cmd_vel" in joined
    assert "output_cmd_vel_topic:=/vel_raw" not in joined
    assert "exit_forward_hold_s:=0.50" in joined
    assert "person_follow.launch.py" in joined
    assert "mode:=identity" in joined
    assert "target_name:=tao" in joined


def test_seek_nearest_uses_person_seek_nearest_mode():
    runner = FakeRunner()
    adapter = RosPersonTaskAdapter(runner=runner, process_checker=lambda _pattern: True)

    result = adapter.control("seek", "nearest")

    joined = "\n".join(runner.commands)
    assert result["ok"] is True
    assert "person_seek.launch.py" in joined
    assert "mode:=nearest" in joined
    assert "target_name" not in joined


def test_support_stack_restarts_obstacle_guard_if_cmd_vel_chain_is_not_running():
    runner = FakeRunner()

    def fake_process_checker(pattern):
        if "Mcnamu_driver_X3" in pattern or "base_node_X3" in pattern or "fused_pose_monitor" in pattern:
            return True
        return False

    adapter = RosPersonTaskAdapter(runner=runner, process_checker=fake_process_checker)

    adapter.ensure_support_stack()

    joined = "\n".join(runner.commands)
    assert "[d]epth_obstacle_guard" in joined
    assert "xargs -r kill" in joined
    assert "obstacle_guard.launch.py" in joined
    assert "output_cmd_vel_topic:=/cmd_vel" in joined


def test_support_stack_does_not_start_duplicate_mcnamu_driver_when_running():
    runner = FakeRunner()

    def fake_process_checker(pattern):
        if "cnamu_driver_X3" in pattern:
            return True
        return False

    adapter = RosPersonTaskAdapter(runner=runner, process_checker=fake_process_checker)

    adapter.ensure_support_stack()

    joined = "\n".join(runner.commands)
    assert "ros2 run yahboomcar_bringup Mcnamu_driver_X3" not in joined


def test_ros_commands_run_under_bash_so_source_setup_works():
    runner = FakeRunner()
    adapter = RosPersonTaskAdapter(runner=runner, process_checker=lambda _pattern: True)

    adapter.control("follow", "nearest")

    assert runner.kwargs
    assert all(item.get("executable") == "/bin/bash" for item in runner.kwargs)


def test_stop_person_tasks_publishes_zero_intent_after_killing_task_nodes():
    runner = FakeRunner()
    adapter = RosPersonTaskAdapter(runner=runner, process_checker=lambda _pattern: True)

    result = adapter.control("stop", "nearest")

    joined = "\n".join(runner.commands)
    assert result["ok"] is True
    assert "xargs -r kill" in joined
    assert "person_task_zero_velocity" in joined
    assert "/cmd_vel_raw" in joined
    assert "'/cmd_vel'" in joined


def test_observe_people_prefers_injected_platform_snapshot_and_starts_tracker():
    runner = FakeRunner()
    posts = []

    def fake_http_post(url, jpeg_bytes):
        posts.append((url, jpeg_bytes))
        return {
            "ok": True,
            "people": [
                {
                    "track_id": 7,
                    "bbox": [240, 20, 400, 420],
                    "unique_name": "tao",
                    "face": {"confidence": 0.83},
                }
            ],
        }

    health_checks = iter([False, True])
    adapter = RosPersonTaskAdapter(
        runner=runner,
        process_checker=lambda _pattern: False,
        snapshot_provider=lambda: b"platform-jpeg",
        http_post=fake_http_post,
        tracker_health_checker=lambda: next(health_checks),
    )

    result = adapter.observe_people()

    joined = "\n".join(runner.commands)
    assert "board_person_tracker_server.py" in joined
    assert posts == [
        (
            "http://127.0.0.1:8102/observe?include_embedding=0&include_face_crop=0",
            b"platform-jpeg",
        )
    ]
    assert result["ok"] is True
    assert result["visible_people"] == [
        {
            "track_id": 7,
            "known": True,
            "name": "tao",
            "position": "center",
            "identity_confidence": 0.83,
        }
    ]
