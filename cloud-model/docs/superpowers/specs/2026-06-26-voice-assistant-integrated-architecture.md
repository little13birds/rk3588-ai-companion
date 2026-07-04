# 语音助手整套逻辑框架记录（master）

更新时间：2026-06-27

板端真源目录：

```text
/home/elf/cloud-model-safety-mainline
```

当前板端分支与最近提交（记录时）：

```text
branch: master
HEAD: 以 `git log -1 --oneline` 为准
```

这份文档记录的是当前 `master` 的整体启动与运行框架；板端工作区目录仍为 `/home/elf/cloud-model-safety-mainline`，这是 Git worktree 路径，不再代表独立功能分支。后续接手时优先以本目录板端代码为准。

## 一句话总览

`./scripts/start_system.sh` 是当前整体启动入口。它先准备 ROS 环境和平台 Orbbec RGB/depth 相机，再启动 `main.py`。`main.py` 内部同时管理语音主循环、ASR/AEC/TTS/LLM、读书机械臂资源调度、安全守护、家长 dashboard，以及语音触发的找人/跟随/避障底盘任务。

重要纠偏：

- `start_system.sh` 本身不直接写着 `person_follow.launch.py`，但启动的 `main.py` 会创建 `PersonTaskController`。
- 当用户语音触发“跟随我 / 找人 / 到 A 身边来”等人物任务时，`person_tasks/ros_adapter.py` 会启动完整底盘深度相机任务栈：底盘驱动、融合位姿、避障、找人或跟随节点。
- 因此，这个整体启动命令确实包含当前项目 1/2/3 的语音触发链路，只是人物任务是按需启动，不是一开机就启动。

## 2026-06-27 实测修复记录

本轮提交覆盖的是已合入 master 的安全守护集成可运行状态，已在板端实测并补充回归测试。重点变化：

- ASR 唤醒后的长句不会再因为超过 5 秒被 idle sleep 打断。`ASRProcessor.is_speaking()` 暴露当前 VAD 状态，主循环只有在 AWAKE、空闲、未处理任务且 `not asr.is_speaking()` 时才允许 idle sleep；VAD 的 speaking true/false 边界都会刷新 `idle_since`。
- 普通模式 `take_photo` 不再默认走机械臂相机，而是通过 `vision.camera.set_snapshot_provider()` 使用平台相机的 safety RGB 缓存。读书模式仍然使用 arm_agent 的 ready 帧。
- 退出读书模式后立刻进行普通视觉问答时，平台 ROS topic 虽已恢复，但 safety 缓存可能尚未收到第一帧。`vision.camera.capture_raw_and_vlm(wait_ready=False)` 现在会最多短等 2 秒、每 100ms 重试平台 snapshot，避免刚恢复相机时误报“摄像头出状况”。
- 读书模式提示语做了去重和顺序优化：进入时先给普通思考反馈，再播放“正在进入读书模式，请稍候。”；真正拍照时使用“我们开始读书吧，我看一下。”；退出时使用“正在退出读书模式，请稍候。”和“已退出读书模式。”。
- 读书模式中唤醒词会暂停读书页处理并进入 AWAKE，用户可继续说“退出读书模式”或普通聊天请求。普通聊天请求会先退出读书资源，再恢复 normal tools 继续对话。
- 人物任务的确定性语音 fallback 已补齐否定短语，例如“别跟着了 / 不要跟着了 / 不用跟着了”会解析为停止跟随，不会误触发启动跟随。
- Dashboard 摄像头 snapshot 会根据运行模式标注来源：normal 使用 `platform_camera`，reading 使用 `reading_arm`，便于前端区分监控画面来源。
- 已加入针对 ASR 状态、读书中断、人物任务解析、平台相机 snapshot、dashboard 摄像头来源的测试，后续改动前应先跑相关测试。

推荐提交前验证命令：

```bash
cd ~/cloud-model-safety-mainline
python3 scripts/test_asr_state_policy.py
python3 scripts/test_reading_interrupt_flow.py
python3 scripts/test_runtime_status_logging.py
python3 -m vision.test_camera
python3 -m dashboard.test_state
python3 -m dashboard.test_server_people
python3 -m llm.test_tool_streaming
python3 -m pytest person_tasks/test_intent.py scripts/test_person_task_main_static.py -q
python3 -m py_compile main.py asr/recognizer.py audio/fillers.py llm/chat.py vision/camera.py dashboard/state.py dashboard/server.py person_tasks/intent.py
```

## 顶层启动链路

启动命令：

```bash
cd ~/cloud-model-safety-mainline
./scripts/start_system.sh
```

主流程：

```text
scripts/start_system.sh
  -> source /opt/ros/humble/setup.bash
  -> source ~/ros2/install/setup.bash
  -> /mnt/sdcard/reconstruct/fix_audio.sh
  -> scripts/start_platform_camera.sh
       -> ros2 launch orbbec_camera orbbec_camera.launch.py ...
       -> 发布 /camera/color/image_raw 和 /camera/depth/image_raw
  -> python3 main.py
       -> AEC + TTS + ASR + LLM
       -> SafetyGuardService
       -> RuntimeCoordinator
       -> DashboardServer
       -> PersonTaskController
```

`scripts/start_platform_camera.sh` 当前默认相机参数：

```bash
ros2 launch orbbec_camera orbbec_camera.launch.py \
  camera_type:=astraproplus \
  enable_ir:=false \
  enable_color:=true \
  enable_depth:=true \
  color_width:=640 \
  color_height:=480 \
  color_fps:=30 \
  depth_width:=640 \
  depth_height:=480 \
  depth_fps:=30
```

不要把平台相机默认改成 20fps。当前板端长期记录里明确：20fps 曾出现 `No matched video stream profile` 风险，已恢复 30fps。

## 主线程音频与对话循环

核心文件：

```text
main.py
asr/recognizer.py
audio/aec_filter.py
tts/realtime_tts.py
llm/chat.py
config.py
```

主循环职责：

1. `arecord` 从 `DEVICE_MIC=plughw:Device,0` 读取 16kHz 单声道 PCM。
2. 音频先进入 `SharedAecFilter` 做 WebRTC AEC 回声消除。
3. 清理后的音频送入 `ASRProcessor.feed()`。
4. ASR 在休眠态跑 KWS，唤醒后跑 VAD + SenseVoice。
5. VAD 断句得到文本后，主线程将 ASR 切回 sleep，并启动后台线程 `process_utterance(text)`。
6. `process_utterance` 判断是否是故事、读书、人物任务或普通对话。
7. 普通对话进入 `Conversation.ask()`，走 qwen3-vl-flash + Function Calling + 流式回复。
8. 流式回复文本实时送入 `RealtimeSpeaker.feed()`，TTS 合成后用 `aplay` 播放，并把播放参考音频送回 AEC。

主状态：

```text
MODE = normal | reading | story
```

唤醒词：

```text
你好小智 / 小智小智
```

打断词：

```text
停 / 停一下 / 停一停 / 停止 / 暂停 / 安静 / 别说 / 先别说
```

打断路径：

```text
ASR on_wake 检测到打断词
  -> interrupt_requested=True
  -> main.handle_interrupt()
  -> speaker.cancel()
  -> aec.reset()
  -> drain mic pipe
  -> 清 ASR 内部 audio queue
  -> 恢复可听状态
```

注意：如果在 `reading` 模式中被打断，会恢复普通 system prompt/tools/speaker speed，并调用读书停止逻辑回初始位。

## LLM 与工具调用

核心文件：

```text
llm/chat.py
person_tasks/tools.py
person_tasks/controller.py
person_tasks/ros_adapter.py
vision/camera.py
sensors/sensors.py
```

默认工具：

```text
take_photo
get_brightness
get_motion
get_temperature
control_person_follow
observe_people_identity
```

`Conversation` 的行为：

- 保存 system + 用户/助手/工具消息。
- 保留滑动窗口，并定期压缩旧对话摘要。
- 普通模式下先让 LLM 判断是否需要 tool call。
- 如果发生 tool call，先执行工具，再把工具结果放回对话，随后开始流式回答。
- 读书模式有快路径：直接调用 `take_photo`，避免先问 LLM 要不要拍照。
- 流式 token 会边打印边送给 TTS。

`take_photo` 在普通/读书模式差异：

- 普通模式：从 `vision.camera.capture_raw_and_vlm(wait_ready=False)` 拿图给 VLM。
- 读书模式：`wait_ready=True`，走机械臂 arm_agent 的 ready 逻辑；还会保存 `reading_captures/`，并尝试 `BookMatchClient` 数据库匹配。匹配成功时返回“逐字朗读以下内容”，避免 VLM 猜文字。

## 读书模式资源调度

核心文件：

```text
main.py
runtime_scheduler/coordinator.py
runtime_scheduler/modes.py
runtime_scheduler/adapters/platform_camera.py
runtime_scheduler/adapters/arm.py
arm/agent_client.py
~/ros2/start_reading_arm.sh
~/ros2/stop_reading_arm.sh
```

进入读书关键词后：

```text
process_utterance()
  -> reading_in_filler()
  -> RuntimeCoordinator.start_reading()
       -> 申请 READING_POLICY 资源
       -> 暂停 safety guard
       -> release 平台 Orbbec 相机（stop 或 suspend，按环境变量）
       -> 确保机械臂 arm_agent 健康
       -> /reading/prepare
       -> /reading/start
  -> 切换 READING_SYSTEM_PROMPT
  -> tools 只保留 take_photo
  -> speaker.speed = 0.9
  -> MODE = reading
```

读完一页后：

```text
Conversation.ask()
  -> reading response
  -> RuntimeCoordinator.pause_reading_page()
       -> /reading/stop?return_home=0
       -> 保持 reading 资源
       -> 平台相机仍保持 release/suspend 状态
       -> 安全守护仍暂停
  -> 如果 should_prompt_next_page(response_text) 为真，播放 reading_continue_filler()
```

继续下一页：

```text
MODE == reading
  -> _start_reading_tracking()
  -> RuntimeCoordinator.start_reading()
  -> 若 reading resources 已持有，复用资源，不重复释放平台相机
```

退出读书：

```text
退出词 / 打断 / 空闲超时
  -> 恢复 normal prompt/tools/speaker speed
  -> RuntimeCoordinator.stop_reading(return_home=True)
       -> /reading/stop?return_home=1
       -> 等待回初始位 settle
       -> stop arm service
       -> 恢复平台相机
       -> 恢复 safety guard
       -> 重新申请 NORMAL_POLICY
```

资源策略：

```text
NORMAL_POLICY:
  MIC_ASR_KWS
  ROS_RGB_CAMERA
  NPU_CORE_0
  NPU_CORE_1

READING_POLICY:
  ROARM_SERIAL
  ARM_AGENT_HTTP
  ROS_RGB_CAMERA
  NPU_CORE_2
  SPEAKER_TTS
```

调度日志重点：

```text
[scheduler] event=reading_start_requested
[scheduler] event=resources_acquired
[scheduler] event=platform_camera_release_ok
[scheduler.arm] event=health_ok / auto_start_done
[scheduler] event=arm_prepare_ok
[scheduler] event=arm_start_ok
[scheduler] event=reading_started
[scheduler] event=normal_restored
```

## 安全守护与平台相机

核心文件：

```text
safety_guard/service.py
safety_guard/ros_camera.py
safety_guard/rknn_runtime.py
safety_guard/monitor.py
safety_guard/analyzer.py
safety_guard/announcer.py
safety_guard/recorder.py
```

启动位置：

```text
main.py
  -> SafetyGuardService(SafetyGuardConfig.from_env(), speaker, cancel_event)
  -> safety_guard.start()
```

运行逻辑：

1. 订阅平台 RGB topic，默认 `/camera/color/image_raw`。
2. 初始化 RKNN runtime，监控 pose/hand/hazard 等候选风险。
3. `SafetyMonitor` 产生 `SafetyCandidate`。
4. 候选进入队列，后台 `_analysis_loop` 调用 `SafetyRiskAnalyzer`。
5. 风险确认后 `SafetyEventRecorder` 记录，`SafetyAnnouncer` 通过 TTS 播报。
6. `camera_snapshot()` 为 dashboard、人物身份观察等提供当前平台 RGB JPEG。

读书模式会暂停安全守护推理，避免平台相机和机械臂相机/NPU 资源互相抢。

## Dashboard 与家长端

核心文件：

```text
dashboard/server.py
dashboard/state.py
dashboard/chassis_control.py
dashboard/parent-dashboard.html
dashboard/client_state.js
```

启动位置：

```text
main.py
  -> DashboardState.from_env()
  -> ChassisControlAdapter.from_env()
  -> start_dashboard_server(dashboard_state)
```

默认端口：

```text
http://192.168.1.113:8080
```

主要接口：

```text
/api/health
/api/camera/snapshot
/api/child/status
/api/environment
/api/conversation/summary
/api/reading/report
/api/activity
/api/alerts
/api/safety/status
/api/system/mode
/api/system/resources
/api/system/features
/api/chassis/*
```

底盘控制：

- `dashboard/chassis_control.py` 默认是保留状态，不主动开车。
- 只有 `DASHBOARD_CHASSIS_CONTROL_ENABLED=1` 时，dashboard 才会发布 `geometry_msgs/Twist` 到 `/cmd_vel_raw`。
- dashboard 不直接发布 `/cmd_vel`；最终速度必须经过 `obstacle_guard` 输出 `/cmd_vel`。

## 语音触发的人物任务（找人 / 跟随 / 身份观察）

核心文件：

```text
person_tasks/intent.py
person_tasks/roles.py
person_tasks/tools.py
person_tasks/controller.py
person_tasks/ros_adapter.py
llm/chat.py
main.py
```

触发方式有两种：

1. 确定性语音意图：`main.py` 在进入 LLM 前调用 `parse_person_task_intent(text)`。
2. LLM Function Calling：系统 prompt 暴露 `control_person_follow` 和 `observe_people_identity` 工具。

确定性意图优先级更高，用来处理高风险移动类命令，避免 LLM 误判。

支持话术示例：

```text
跟着我 / 跟我走 / 跟我来 -> follow nearest
找一下 / 找找 / 在哪里 / 到我身边 -> seek nearest 或指定目标
不要跟了 / 停止跟随 / 不要找了 -> stop
你知道我是谁吗 / 前面都有谁 -> observe_people_identity
```

角色映射：

```text
A / 角色A / tao / 涛 -> tao
B / 角色B / xiao / 小 -> xiao
我 / 最近的人 / nearest -> nearest
```

人物任务启动链路：

```text
main.py
  -> parse_person_task_intent(text)
  -> execute_person_tool("control_person_follow", ...)
  -> PersonTaskController.control(action, target)
  -> RosPersonTaskAdapter.control(action, target)
       -> ensure_support_stack()
       -> stop_person_tasks()
       -> _start_person_follow(target) 或 _start_person_seek(target)
```

`ensure_support_stack()` 当前会按需启动：

```text
ros2 run yahboomcar_bringup Mcnamu_driver_X3
ros2 run yahboomcar_base_node base_node_X3 --ros-args -p pub_odom_tf:=false
ros2 run depth_camera_perception fused_pose_monitor --ros-args ...
ros2 launch depth_camera_perception obstacle_guard.launch.py ...
```

注意：历史上重复启动多个 `Mcnamu_driver_X3` 会导致 `/vel_raw` 等状态异常。接手排查时先 `ps` 看是否有重复底盘节点。

找人启动：

```text
ros2 launch depth_camera_perception person_seek.launch.py
```

跟随启动：

```text
ros2 launch depth_camera_perception person_follow.launch.py
```

当前 `person_tasks/ros_adapter.py` 中跟随/找人命令仍有一些旧硬编码参数，例如：

```text
follow_max_forward_mps:=0.25
follow_max_angular_z:=0.20
search_angular_z:=0.20
```

但是当前 `~/ros2/src/depth_camera_perception/launch/person_follow.launch.py` 已默认加载参数文件：

```text
~/ros2/src/depth_camera_perception/config/person_follow_params.yaml
```

实测短时参数探针确认，`person_follow` 最终读取到的是 YAML 中的：

```text
search_angular_z = 1.25
follow_max_angular_z = 0.25
center_tolerance_fraction = 0.02
```

也就是说：当前整体语音助手触发跟随时，跟随参数文件会生效。为避免后续误导，建议下一步把 `person_tasks/ros_adapter.py` 里旧的 `0.20` 硬编码清掉，改成显式传：

```text
params_file:=/home/elf/ros2/src/depth_camera_perception/config/person_follow_params.yaml
```

跟随参数现场调试文件：

```bash
nano ~/ros2/src/depth_camera_perception/config/person_follow_params.yaml
```

常调项：

```yaml
follow_distance_m: 1.20
distance_tolerance_m: 0.08
follow_max_forward_mps: 0.40
follow_linear_gain: 1.0
follow_angular_gain: 0.8
follow_max_angular_z: 0.25
center_tolerance_fraction: 0.02
search_angular_z: 1.25
target_lost_timeout_s: 0.50
```

如果丢失目标后旋转仍慢，优先调大：

```yaml
search_angular_z: 2.0
```

如果人在框内移动时对准不够积极，优先调：

```yaml
follow_angular_gain: 1.2
follow_max_angular_z: 0.45
center_tolerance_fraction: 0.015
```

## 深度相机项目与语音助手的关系

ROS 包真源：

```text
~/ros2/src/depth_camera_perception
```

相关节点：

```text
fused_pose_monitor
obstacle_guard
obstacle_web_monitor
person_seek
person_follow
person_speed_alert
person_web_monitor
```

项目映射：

```text
项目 1 找人:
  person_seek
  语音触发 action=seek

项目 2 避障:
  obstacle_guard
  所有人物任务和 dashboard 底盘控制都应走 /cmd_vel_raw -> obstacle_guard -> /cmd_vel

项目 3 跟随:
  person_follow
  语音触发 action=follow

项目 4 速度检测:
  person_speed_alert
  当前不是 start_system.sh 默认链路的一部分，后续接 dashboard/手机报警时再接入
```

人物任务栈里避障始终是安全层。不要让语音助手、dashboard 或人物任务直接绕过 `obstacle_guard` 发布 `/cmd_vel`。

## `start_system.sh` 与人物任务的关系

容易误判点：

- `start_system.sh` 默认只启动平台相机和 `main.py`。
- 人物任务的 ROS 节点不是启动脚本立即启动，而是在语音命令触发后由 `PersonTaskController` 拉起。
- 因此，刚启动系统时看不到 `person_follow` 是正常的。
- 说“跟着我”后才会看到 `person_follow`、`obstacle_guard`、底盘相关节点按需出现。

建议排查命令：

```bash
ps -eo pid,args | grep -E 'person_follow|person_seek|obstacle_guard|Mcnamu_driver_X3|base_node_X3|fused_pose_monitor' | grep -v grep
ros2 node list
ros2 topic list | grep -E 'cmd_vel|obstacle|person_follow|person_seek|fused_pose'
```

## 常用启动与验证

整体启动：

```bash
cd ~/cloud-model-safety-mainline
./scripts/start_system.sh
```

状态查看：

```bash
./scripts/start_system.sh --status
./scripts/start_platform_camera.sh --status
```

CLI debug：

```bash
./scripts/start_system.sh --cli-debug
```

Dashboard：

```text
http://192.168.1.113:8080
```

跟随网页（语音触发跟随后才有）：

```text
http://192.168.1.113:8093
```

找人网页（语音触发找人后才有）：

```text
http://192.168.1.113:8092
```

避障网页：

```text
http://192.168.1.113:8090
```

融合位姿网页：

```text
http://192.168.1.113:8091
```

## 当前已知风险与后续建议

1. `person_tasks/ros_adapter.py` 仍保留旧跟随调参硬编码。虽然当前 launch 参数文件会覆盖它，但建议清理并显式传 `params_file`。
2. `ensure_support_stack()` 会按进程匹配启动 `Mcnamu_driver_X3`，历史上重复底盘驱动会导致状态异常。排查移动问题先查重复进程。
3. `项目 4 person_speed_alert` 尚未纳入 `start_system.sh` 默认链路；语音 TTS/手机报警接入还需要单独设计。
4. `config.py` 当前含 API key，长期文档和对外提交不要复制具体 key。
5. 读书模式与平台相机共享 USB/ROS/NPU 资源，出问题时按 scheduler 日志定位：平台相机 release/restore、arm health、safety pause/resume。
6. Dashboard 底盘控制默认关闭，开启前确认 `obstacle_guard` 正常运行，且最终链路是 `/cmd_vel_raw -> obstacle_guard -> /cmd_vel`。

## 后续 agent 接手优先阅读文件

```text
CLAUDE.md
scripts/start_system.sh
scripts/start_platform_camera.sh
main.py
llm/chat.py
asr/recognizer.py
tts/realtime_tts.py
runtime_scheduler/coordinator.py
runtime_scheduler/adapters/platform_camera.py
runtime_scheduler/adapters/arm.py
safety_guard/service.py
dashboard/chassis_control.py
person_tasks/intent.py
person_tasks/controller.py
person_tasks/ros_adapter.py
```

ROS 侧同步阅读：

```text
~/ros2/src/depth_camera_perception/depth_camera_perception/person_follow.py
~/ros2/src/depth_camera_perception/depth_camera_perception/person_follow_node.py
~/ros2/src/depth_camera_perception/launch/person_follow.launch.py
~/ros2/src/depth_camera_perception/config/person_follow_params.yaml
~/ros2/src/depth_camera_perception/depth_camera_perception/obstacle_avoidance.py
~/ros2/src/depth_camera_perception/depth_camera_perception/obstacle_guard_node.py
```
