"""ROS process adapter for person seek/follow tasks.

The adapter starts high-level ROS nodes only. Motion intent is normally
published by person_seek/person_follow on /cmd_vel_raw and obstacle_guard
remains responsible for the final /cmd_vel. Stop/cleanup is the exception:
it publishes repeated zero velocity to both topics so the chassis driver cannot
keep executing a stale command after task nodes exit.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, Optional


Runner = Callable[..., subprocess.CompletedProcess]
ProcessChecker = Callable[[str], bool]
SnapshotProvider = Callable[[], Optional[bytes]]
HttpPost = Callable[[str, bytes], Dict[str, object]]
HealthChecker = Callable[[], bool]


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


class RosPersonTaskAdapter:
    def __init__(
        self,
        *,
        ros_root: str = "~/ros2",
        ros_domain_id: str = "30",
        runner: Optional[Runner] = None,
        process_checker: Optional[ProcessChecker] = None,
        tracker_url: Optional[str] = None,
        snapshot_provider: Optional[SnapshotProvider] = None,
        http_post: Optional[HttpPost] = None,
        tracker_health_checker: Optional[HealthChecker] = None,
    ):
        self.ros_root = os.path.expanduser(ros_root)
        self.ros_domain_id = str(ros_domain_id)
        self.runner = runner or subprocess.run
        self.process_checker = process_checker or self._process_running
        self.snapshot_provider = snapshot_provider
        self.http_post = http_post or self._http_post_json
        self.tracker_health_checker = tracker_health_checker or self._tracker_healthy
        self.tracker_url = tracker_url or os.environ.get(
            "PERSON_TRACKER_URL",
            "http://127.0.0.1:8102",
        )

    def control(self, action: str, target: str) -> Dict[str, object]:
        action = str(action or "").strip().lower()
        target = str(target or "nearest").strip() or "nearest"
        if action == "stop":
            self.stop_person_tasks()
            return {"ok": True, "action": "stop", "target": target}
        if action not in {"follow", "seek"}:
            return {"ok": False, "error": "invalid_action", "action": action, "target": target}

        self.ensure_support_stack()
        self.stop_person_tasks()
        if action == "follow":
            self._start_person_follow(target)
        else:
            self._start_person_seek(target)
        return {"ok": True, "action": action, "target": target, "target_name": target}

    def observe_people(self) -> Dict[str, object]:
        raw_jpg = self._capture_observation_jpeg()
        if not raw_jpg:
            return {"ok": False, "error": "capture_empty"}

        self.ensure_tracker_server()
        url = self.tracker_url.rstrip("/") + "/observe?include_embedding=0&include_face_crop=0"
        try:
            payload = self.http_post(url, raw_jpg)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return {
                "ok": False,
                "error": "person_tracker_unavailable",
                "error_type": type(exc).__name__,
                "url": self.tracker_url,
            }
        return self._compact_observation(payload)

    def _capture_observation_jpeg(self) -> bytes:
        if self.snapshot_provider is not None:
            try:
                jpg = self.snapshot_provider()
                if jpg:
                    return jpg
            except Exception:
                pass
        try:
            from vision.camera import capture_raw_and_vlm

            raw_jpg, _img_b64 = capture_raw_and_vlm(wait_ready=False)
            return raw_jpg or b""
        except Exception:
            return b""

    def ensure_tracker_server(self) -> None:
        if self.tracker_health_checker():
            return
        if not self.process_checker("[b]oard_person_tracker_server.py"):
            self._run_bg(
                "person_tracker_http",
                "cd /home/elf/face_identity_rk3588 && "
                "python3 scripts/board_person_tracker_server.py --host 127.0.0.1 --port 8102",
                raw_command=True,
            )
        for _ in range(16):
            time.sleep(0.5)
            if self.tracker_health_checker():
                return

    def _http_post_json(self, url: str, jpeg_bytes: bytes) -> Dict[str, object]:
        req = urllib.request.Request(
            url,
            data=jpeg_bytes,
            headers={"Content-Type": "image/jpeg"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _tracker_healthy(self) -> bool:
        try:
            with urllib.request.urlopen(self.tracker_url.rstrip("/") + "/health", timeout=0.5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def ensure_support_stack(self) -> None:
        if not self.process_checker("[M]cnamu_driver_X3"):
            self._run_bg(
                "mcnamu_driver_person_task",
                "ros2 run yahboomcar_bringup Mcnamu_driver_X3",
            )
        if not self.process_checker("[b]ase_node_X3"):
            self._run_bg(
                "base_node_person_task",
                "ros2 run yahboomcar_base_node base_node_X3 --ros-args -p pub_odom_tf:=false",
            )
        if not self.process_checker("[f]used_pose_monitor"):
            self._run_bg(
                "fused_pose_person_task",
                "ros2 run depth_camera_perception fused_pose_monitor --ros-args "
                "-p web_port:=8091 -p publish_period_s:=0.02 "
                "-p yaw_rate_scale:=1.53 -p linear_x_scale:=0.9",
            )
        if not self.process_checker("[o]bstacle_guard.launch.py.*output_cmd_vel_topic:=/cmd_vel"):
            self._stop_obstacle_guard()
            self._run_bg(
                "obstacle_guard_person_task",
                "ros2 launch depth_camera_perception obstacle_guard.launch.py "
                "dry_run:=false allow_bypass:=true use_fused_pose_bypass:=true "
                "input_cmd_vel_topic:=/cmd_vel_raw output_cmd_vel_topic:=/cmd_vel "
                "front_invalid_depth_block_fraction:=0.90 side_invalid_depth_block_fraction:=0.90 "
                "normal_forward_mps:=0.40 bypass_forward_mps:=0.20 "
                "return_heading_angular_sign:=-1.0 avoid_min_forward_s:=1.00 "
                "side_clear_hold_s:=1.00 exit_forward_hold_s:=0.50",
            )

    def _stop_obstacle_guard(self) -> None:
        self._run(
            "ps -eo pid,args | awk '/[o]bstacle_guard.launch.py|[d]epth_obstacle_guard/ {print $1}' "
            "| xargs -r kill"
        )

    def stop_person_tasks(self) -> None:
        self._publish_zero_velocity()
        self._run(
            "ps -eo pid,args | awk '/[p]erson_seek.launch.py|[d]epth_camera_perception\\/person_seek|"
            "[p]erson_follow.launch.py|[d]epth_camera_perception\\/person_follow/ {print $1}' | xargs -r kill"
        )
        self._publish_zero_velocity()

    def _publish_zero_velocity(self) -> None:
        command = (
            f"cd {shlex.quote(self.ros_root)} && "
            "source /opt/ros/humble/setup.bash && "
            "source install/setup.bash && "
            f"export ROS_DOMAIN_ID={shlex.quote(self.ros_domain_id)} && "
            "python3 - <<'PY'\n"
            "import time\n"
            "import rclpy\n"
            "from geometry_msgs.msg import Twist\n"
            "rclpy.init(args=None)\n"
            "node = rclpy.create_node('person_task_zero_velocity')\n"
            "topics = ['/cmd_vel_raw', '/cmd_vel']\n"
            "pubs = [node.create_publisher(Twist, topic, 10) for topic in topics]\n"
            "msg = Twist()\n"
            "for _ in range(20):\n"
            "    for pub in pubs:\n"
            "        pub.publish(msg)\n"
            "    rclpy.spin_once(node, timeout_sec=0.02)\n"
            "    time.sleep(0.05)\n"
            "node.destroy_node()\n"
            "rclpy.shutdown()\n"
            "PY"
        )
        self._run(command)

    def _start_person_follow(self, target: str) -> None:
        mode, target_arg = self._mode_and_target_arg(target)
        self._run_bg(
            "person_follow_task",
            "ros2 launch depth_camera_perception person_follow.launch.py "
            f"mode:={mode} {target_arg} "
            "detector_backend:=pose_rknn "
            "model_path:=/home/elf/face_identity_rk3588/models/rknn/pose_yolov8n_hybrid.rknn "
            "auto_start:=true web_port:=8093 "
            "follow_max_forward_mps:=0.25 follow_max_angular_z:=0.20 search_angular_z:=0.20",
        )

    def _start_person_seek(self, target: str) -> None:
        mode, target_arg = self._mode_and_target_arg(target)
        self._run_bg(
            "person_seek_task",
            "ros2 launch depth_camera_perception person_seek.launch.py "
            f"mode:={mode} {target_arg} "
            "detector_backend:=pose_rknn "
            "model_path:=/home/elf/face_identity_rk3588/models/rknn/pose_yolov8n_hybrid.rknn "
            "auto_start:=true web_port:=8092 "
            "search_angular_z:=0.20 approach_max_forward_mps:=0.25 "
            "approach_slow_forward_mps:=0.08 approach_max_angular_z:=0.20",
        )

    def _mode_and_target_arg(self, target: str) -> tuple[str, str]:
        if target == "nearest":
            return "nearest", ""
        return "identity", "target_name:=" + shlex.quote(target)

    def _run_bg(self, label: str, ros_command: str, *, raw_command: bool = False) -> None:
        log_path = f"/tmp/{label}.log"
        if raw_command:
            command = f"setsid -f nohup bash -lc {shlex.quote(ros_command)} > {shlex.quote(log_path)} 2>&1 </dev/null"
        else:
            command = (
                f"cd {shlex.quote(self.ros_root)} && "
                "source /opt/ros/humble/setup.bash && "
                "source install/setup.bash && "
                f"export ROS_DOMAIN_ID={shlex.quote(self.ros_domain_id)} && "
                f"setsid -f nohup {ros_command} > {shlex.quote(log_path)} 2>&1 </dev/null"
            )
        self._run(command)

    def _run(self, command: str):
        return self.runner(
            command,
            shell=True,
            executable="/bin/bash",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20.0,
        )

    @staticmethod
    def _compact_observation(payload: Dict[str, object]) -> Dict[str, object]:
        people = []
        for person in payload.get("people", []) if isinstance(payload, dict) else []:
            if not isinstance(person, dict):
                continue
            face = person.get("face") if isinstance(person.get("face"), dict) else {}
            name = person.get("unique_name") or face.get("display_name") or face.get("person_id")
            bbox = person.get("bbox") or []
            position = _position_from_bbox(bbox)
            people.append({
                "track_id": person.get("track_id"),
                "known": bool(name),
                "name": name,
                "position": position,
                "identity_confidence": face.get("confidence"),
            })
        return {
            "ok": bool(payload.get("ok", True)) if isinstance(payload, dict) else False,
            "visible_people": people,
            "raw_count": len(people),
        }

    @staticmethod
    def _process_running(pattern: str) -> bool:
        result = subprocess.run(
            f"ps -eo args | grep -E '{pattern}' | grep -v grep",
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3.0,
        )
        return result.returncode == 0 and bool(result.stdout.strip())


def _position_from_bbox(bbox) -> str:
    try:
        x1, _y1, x2, _y2 = [float(v) for v in bbox[:4]]
    except Exception:
        return "unknown"
    cx = (x1 + x2) * 0.5
    if cx < 213:
        return "left"
    if cx > 426:
        return "right"
    return "center"
