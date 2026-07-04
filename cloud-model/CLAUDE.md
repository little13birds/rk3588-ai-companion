# 云端模型 — AI 语音助手（打断版 + 读书模式 + 安全守护）

> **板端是真源**：所有代码修改在板端 (`elf@192.168.1.113:~/cloud-model/`) 进行，改完验证通过后 scp 同步到 Windows (`D:\嵌赛项目\云端模型\`)。不要从 Windows git 向板端同步代码。板端 git 和 Windows git 是独立仓库，commit hash 不同。

## 项目概述

基于 RK3588 嵌入式开发板的 AI 语音助手，实现完整 ASR → VLM → TTS 全流程 + Function Calling + 打断功能 + AEC 回声消除 + 持续读书模式，并在 `master` 基线之上加载安全守护后台模块和家长看护仪表盘后端。

```
[麦克风] → [AEC] → [本地 ASR] → [云端 VLM/LLM] → [云端 TTS] → [扬声器]
  arecord   WebRTC   SenseVoice    qwen3-vl-flash   qwen3-tts-*-realtime    aplay
  16kHz    回声消除   + VAD 断句    + FunctionCall    + WebSocket实时流式      stdin
                      + KWS 唤醒词   + 滑动窗口        + 逐句多音色切换  24kHz
                      + 打断检测     + 压缩摘要
                       [摄像头] ───→ take_photo 工具 (OpenCV /dev/video21)
                       [GY-30] ────→ get_brightness (I2C 0x23)
                       [MPU6050] ──→ get_motion (I2C 0x68)
                       [DHT11] ────→ get_temperature (GPIO /home/elf/dht11)
```

## 板端目录 `~/cloud-model/`

```
├── main.py               # 主入口，非阻塞主循环 + 打断处理 + MODE 状态机
├── debug_runtime.py      # 手动 CLI 调试入口: 复现读书/相机调度，不接 ASR/LLM/TTS
├── config.py             # API Key、设备、模型参数
├── asr/recognizer.py     # ASRProcessor (SenseVoice + VAD + KWS) 线程安全版
├── llm/chat.py           # Conversation (多轮对话 + 压缩摘要 + ToolCall + 打断标记)
├── tts/realtime_tts.py   # RealtimeSpeaker 实时 WebSocket TTS (合成线程+播放线程+aplay stdin) + switch_voice/cancel/wait
├── tts/phrase_cache.py   # 全局固定语录 WAV 缓存，未命中时用当前 TTS 参数现场生成
├── tts/synthesizer.py    # 已精简为常量文件 (旧 StreamSpeaker/HTTP TTS 已删)
├── vision/camera.py      # OpenCV 持久连接拍照 → base64 (首次预热,后续10ms取帧)
├── sensors/sensors.py    # GY-30/MPU6050/DHT11 传感器读取
├── safety_guard/         # 安全守护: ROS RGB + RKNN pose/hand/hazard + VLM复核 + 固定语录报警
├── safety_guard_native/  # RKNN C++ runtime 源码，构建 libsafety_rknn.so
├── runtime_scheduler/    # 运行时资源调度: mode/lease/NPU core/读书-安全暂停协调/dashboard 状态
├── scripts/
│   ├── start_system.sh   # 统一启动: platform camera + cloud-model + safety/dashboard
│   ├── start_platform_camera.sh # 平台深度/RGB 相机 ROS publisher
│   ├── stop_platform_camera.sh  # 停止平台深度/RGB 相机 ROS publisher
│   ├── suspend_platform_camera.sh # 通过 Orbbec toggle service 挂起平台 RGB/depth 流
│   └── resume_platform_camera.sh  # 通过 Orbbec toggle service 恢复平台 RGB/depth 流
├── dashboard/            # 家长看护仪表盘 HTTP API，默认 0.0.0.0:8080
├── audio/
│   ├── aec_filter.py     # 线程安全 AEC 封装 (SharedAecFilter)
│   ├── aec_bridge.cpp    # WebRTC AEC C++ 桥接 → libaec_bridge.so
│   ├── echo_cancel.py    # ctypes 封装 EchoCanceller
│   ├── fillers.py        # 固定语录文本池，走 tts/phrase_cache.py 动态生成/缓存
│   ├── generated_phrases/ # 运行时生成的固定语录 WAV 缓存，不提交 Git
│   ├── fillers/*.wav     # 预生成 WAV（wake_/think_/photo_/reading_in/reading_out/interrupt_）
│   └── silence.wav       # 句间静音
├── test_interrupt.py     # KWS 灵敏度测试（需人声）
├── test_interrupt_auto.py # 打断链路自动化测试（程序模拟 on_wake）
├── test_kws_sensitivity.py # KWS 安静/播放环境对比测试
└── backup/               # 原始代码备份 (2024-05-23)
```

## 硬件环境

| 组件 | 详情 |
|------|------|
| SoC | RK3588, 8GB RAM |
| 麦克风 | USB 音频设备 (`plughw:Device,0`) |
| 扬声器 | USB 音频设备 (`plughw:Device,0`) |
| 摄像头 | SunplusIT OPENAICAM (`/dev/video21`, 640×480) |
| SD 卡 | 29G ext4, fstab 固定挂载 `/mnt/sdcard` |
| SSH | `ssh elf@192.168.1.113` 免密 |

## 关键配置

```python
API Key: 见 config.py (TTS_API_KEY, 不提交 Git)
VLM:   qwen3-vl-flash  @ dashscope.aliyuncs.com/compatible-mode/v1
TTS:   qwen3-tts-instruct-flash-realtime (WebSocket 实时) @ dashscope
       输出 24kHz PCM → aplay stdin 流式播放, 句间 200ms 静音
       多音色: 故事模式7角色音色 (Cherry/Ethan/Serena/Stella/Moon/EldricSage/Pip)
              LLM用[VoiceName]标注, RealtimeSpeaker逐句 switch_voice 切换(断连重连)
固定语录: tts/phrase_cache.py 全局缓存。未生成过的固定文本用当前 TTS 参数现场生成 WAV，
         缓存到 audio/generated_phrases/；后续直接播放缓存。
         唤醒回应只使用 `我在。` / `请说。` 等短句池，不使用旧 wake_*.wav fallback，
         避免提示音覆盖用户紧接着说的问题开头。
平台深度相机: 默认由 `scripts/start_system.sh` 先启动 `scripts/start_platform_camera.sh`，
         通过 Orbbec/Astra ROS publisher 提供 `/camera/color/image_raw` 和 `/camera/depth/image_raw`。
         默认 launch 为 `ros2 launch orbbec_camera orbbec_camera.launch.py camera_type:=astraproplus enable_ir:=false enable_color:=true enable_depth:=true color_width:=640 color_height:=480 color_fps:=30 depth_width:=640 depth_height:=480 depth_fps:=30`，
         使用 Orbbec ROS 驱动显式 640x480@30fps 配置；不要把平台相机默认改成 20fps，
         该配置在当前板端会触发 `No matched video stream profile`。
         启动平台相机前，统一启动脚本会先调用 `~/ros2/stop_reading_arm.sh` 释放上次残留的读书相机服务；
         这是 normal/CLI debug 默认行为，防止 `arm_agent` 残留导致平台相机无 RGB 首帧。
         如需保留已启动的读书 arm，可加 `--keep-arm-before-platform`。
         若已有未被 PID 文件记录的平台相机进程，停止脚本会 fallback 匹配 Orbbec camera launch/node 并停止，
         以便读书模式释放 USB 带宽。
         启动健康检查必须等到 `/camera/color/image_raw` 实际收到一帧；仅有 publisher 不算成功。
         如果 publisher 存在但无彩色帧，脚本会停止坏进程并重试一次，仍失败则返回非零。
         runtime scheduler 调用平台相机脚本的外层超时为 `SCHEDULER_PLATFORM_CAMERA_SCRIPT_TIMEOUT_SEC=45.0`，
         必须大于 `start_platform_camera.sh` 内部健康检查和一次重试的最大耗时，避免脚本还在恢复时被调度器提前杀掉。
         平台相机释放策略由 `SCHEDULER_PLATFORM_CAMERA_RELEASE_MODE` 控制：
         `stop` 为旧路径，进入读书时 kill/relaunch 平台相机进程；`suspend` 为新路径，进入读书时调用
         `/camera/toggle_color false` 和 `/camera/toggle_depth false`，保留 Orbbec node 但停止 RGB/depth 出帧；
         退出读书时调用 depth/color true 恢复首帧。`SCHEDULER_PLATFORM_CAMERA_SUSPEND_FALLBACK_TO_STOP=1`
         会在 toggle 失败时回退旧 stop/start 路径。实测 suspend 后 color/depth 3s 内无帧，机械臂相机可启动，
         resume 后平台 color/depth 约 1.6-1.9s 恢复首帧。
         正式路径为了速度，`suspend_platform_camera.sh` 默认不等待“无帧”验证；
         诊断时设置 `PLATFORM_CAMERA_SUSPEND_VERIFY_NO_FRAME=1`。`resume_platform_camera.sh`
         默认只等待 RGB 首帧；如需同时等待 depth 首帧，设置 `PLATFORM_CAMERA_RESUME_REQUIRE_DEPTH_FRAME=1`。
         这是车体/平台相机，不是机械臂读书相机。
机械臂读书相机: 由 `~/ros2/start_reading_arm.sh` 内的 `arm_agent` 管理，默认 V4L2 `/dev/video21`。
         默认不在系统启动时占用相机；进入读书模式时由 runtime scheduler 按需自动启动。
         `--with-arm` 可在启动时预拉起机械臂服务，`--no-auto-start-arm` 可禁用读书模式按需启动。
安全守护: 默认随 cloud-model 启动，订阅平台 ROS RGB + RKNN pose/hand/hazard。
         pose target 默认 20Hz；hand/hazard 默认每 0.2s 一次；VLM 只复核风险。
         现场播报使用固定语录池并有 20s 同类冷却。需要临时关闭时设置 SAFETY_GUARD_ENABLED=0。
资源调度: 默认开启 `runtime_scheduler`。读书模式会申请 arm_agent/机械臂/NPU core/TTS 资源，
         并暂停安全 RKNN 推理；退出或启动失败后恢复 normal + 安全守护。
         调度层已新增 RK3588 `npu_core_0/1/2` 物理 core 资源；normal 默认申请 core0/core1，
         reading 默认申请 core2。当前只是调度表达，RKNN C++ runtime 的 core-mask 绑定仍是后续任务。
         回滚设置 `RESOURCE_SCHEDULER_ENABLED=0`。默认 `SCHEDULER_AUTO_START_READING_ARM=1`，
         进入读书模式时顺序为：暂停安全推理 -> 释放平台相机(stop 或 suspend) -> 启动/检查机械臂读书服务 ->
         `/reading/prepare` -> `/reading/start`；退出或启动失败后重启平台相机并恢复安全守护。
         页间继续读书会复用已持有的 reading 资源；`page-done` 后再 `next-page` 时应看到
         `reading_resources_reused`、`safety_pause_skipped`、`platform_camera_release_skipped`，
         不应再次出现平台相机 release/start/stop。
         自动拉起机械臂服务后，会继续等待 `/book/status` 和 `/frame.jpg` 健康，避免 arm_agent 刚启动
         但首帧还没热起来时被误判失败；等待时间 `SCHEDULER_ARM_START_HEALTH_WAIT_SEC=8.0`，
         轮询间隔 `SCHEDULER_ARM_START_HEALTH_POLL_SEC=0.5`。
         调度日志前缀为 `[scheduler]`、`[scheduler.arm]`、`[scheduler.platform_camera]`。
         正常进入读书应看到 `reading_start_requested` -> `resources_acquired` ->
         `platform_camera_release_ok` -> `arm_health_ok` -> `arm_prepare_ok` ->
         `arm_start_ok` -> `reading_started`；退出应看到 `reading_stop_requested` ->
         `arm_stop_ok` -> `platform_camera_restore_ok` -> `normal_restored`。
家长看护: 默认随 cloud-model 启动 `dashboard/` HTTP 服务，端口 8080。
         需要临时关闭时设置 DASHBOARD_ENABLED=0；端口可用 DASHBOARD_PORT 覆盖。
         摄像头快照复用安全守护缓存的平台 ROS RGB 帧，不直接抢读书机械臂相机。
         首页只做状态预览；移动/找孩子/急停已拆到独立“控制”页。
         底盘控制默认随 `./scripts/start_system.sh` 启用；设置 DASHBOARD_CHASSIS_CONTROL_ENABLED=0 可关闭。
         启用后，main 会复用 person_tasks 的 ROS adapter 拉起底盘支持栈
         (`Mcnamu_driver_X3`、`base_node_X3`、`fused_pose_monitor`、`obstacle_guard`)。
         dashboard/chassis_control.py 会在 cloud-model 进程内发布 geometry_msgs/Twist 到 `/cmd_vel_raw`，
         由现有 obstacle_guard 输出最终 `/cmd_vel`。不要从家长端绕过避障直接发布 `/cmd_vel`。
         默认速度对齐 `teleop_twist_keyboard`：linear.x=0.5、angular.z=1.0；如需低速测试可用环境变量覆盖。
         家长端方向键按住时会周期重发 `/cmd_vel_raw` 意图，松开/失焦/隐藏页面立即发 stop；
         若 `/depth_camera/obstacle_status` 报 blocked，obstacle_guard 仍会把最终 `/cmd_vel` 压到 0。
         读书模式或人物寻人/跟随任务 active 时，家长端方向键由后端返回 busy，前端禁用按钮；
         急停/stop 不被拦截。
         “实时通信”目前指前端预留的 WebSocket/SSE 推送通道；现在页面移动控制仍走 HTTP `/api/move`
         周期重发，ROS 内部的实时控制链路是 `/cmd_vel_raw -> obstacle_guard -> /cmd_vel -> driver_node`。
         家长端消息和入睡提醒进入主循环 TTS 队列；播放前会 reset speaker，避免上一次打断后的 cancel 状态吞掉提醒。
         助眠开始/停止目前只管理 dashboard 计时和睡眠状态，不播放白噪音/音乐/故事音频。
         前端会使用 start/stop API 返回状态立即刷新 UI，不依赖 30s 睡眠状态轮询。
         睡眠设置持久化到 dashboard SQLite；久未入睡判断需要到点、超过宽限时间、且配置的孩子 unique_name 最近可见。
         `/api/environment` 首次请求会同步读取一次温湿度/光照，避免网页刚打开显示 0；若传感器还没准备好，
         前端遇到 `errors=["not_ready"]` 会短延迟重试，不把 0 写入 UI。
         历史画面中的读书拍摄会读取 `reading_captures/*.json` 的 `book_detection.pages[].corners`，
         在前端预览图上叠加每页 TL/TR/BR/BL 角点，方便检查书页定位质量。
设备:  麦克风/扬声器统一 plughw:Device,0 (USB 音频设备)
ASR:   SenseVoice (sherpa-onnx) @ /home/elf/Desktop/reconstruct/model/sensevoice/
       VAD silero threshold=0.7, min_silence=800ms
       (软链接 → /mnt/sdcard/reconstruct, fstab 固定挂载)
KWS:   唤醒词 "你好小智" "小智小智"
       打断词 "停" "停一下" "停一停" "别说" "先别说"
       (模型中仍含 "安静" "暂停"，但代码已禁用)
       默认关闭周期进度日志；需要诊断时设置 `ASR_KWS_PROGRESS_EVERY=50`，会在同一行刷新 chunks/qsize。
AEC:   WebRTC AudioProcessing @ libwebrtc-audio-processing1:arm64
pip:   阿里云镜像 ~/.config/pip/pip.conf
conda: 已禁用（系统 Python 3.10.12）
```

## 启动方式

推荐使用统一启动脚本:

```bash
ssh elf@192.168.1.113
cd ~/cloud-model-safety-mainline
./scripts/start_system.sh
```

常用测试命令:

```bash
./scripts/start_system.sh --dry-run --no-main   # 只检查将执行的启动动作
./scripts/start_system.sh --cli-debug           # 同一启动流程，但进入手动 CLI 调试，不接 ASR/LLM/TTS
./scripts/start_system.sh --background          # 后台启动，日志写入 logs/
./scripts/start_system.sh --status              # 查看后台 PID 和 dashboard health
./scripts/start_system.sh --stop                # 停止后台 cloud-model
./scripts/start_system.sh --no-platform-camera  # 不启动平台深度/RGB 相机
./scripts/start_system.sh --with-arm            # 启动前额外拉起 ~/ros2/start_reading_arm.sh
./scripts/start_system.sh --keep-arm-before-platform # 不在平台相机启动前清理残留读书 arm
./scripts/start_platform_camera.sh --status     # 单独查看平台相机 RGB/depth publisher
./scripts/start_system.sh --cli-debug           # 默认使用 suspend 释放平台相机
```

脚本默认启用 platform camera/safety/dashboard/runtime_scheduler，并自动加载 `/opt/ros/humble/setup.bash` 与 `~/ros2/install/setup.bash`。`--status` 会同时检查 `/camera/color/image_raw` publisher 数；如果 publisher 为 0，安全守护进程仍会运行但不会收到相机帧。读书机械臂默认不在启动时占用读书相机，且默认启动 normal/CLI debug 时会先停止上次残留的读书 arm 服务再拉起平台相机；`SCHEDULER_AUTO_START_READING_ARM=1` 会让调度器在进入读书模式时自动拉起 `~/ros2/start_reading_arm.sh`；需要启动时预热机械臂服务再加 `--with-arm`。

动态相机切换的一键测试入口仍是 `./scripts/start_system.sh`。启动后平台相机先运行，安全守护订阅平台 ROS RGB；说“进入读书模式”后 runtime scheduler 会释放平台相机资源、按需启动机械臂读书相机服务。默认释放策略为 `suspend`，即调用 Orbbec toggle service 关闭 RGB/depth 出帧但保留 node；如需回退旧 kill/relaunch 路径，可临时使用 `SCHEDULER_PLATFORM_CAMERA_RELEASE_MODE=stop ./scripts/start_system.sh --cli-debug`。读完一页后的页间暂停只停止机械臂 tracking，保持 reading 资源、平台相机释放状态、arm 服务运行和安全守护暂停；继续下一页时应复用这些资源并打印 `reading_resources_reused`，避免每页都触发 Orbbec 重新枚举或重复 toggle。只有真正退出读书/启动失败后，调度器才会先发 `return_home=1`，等待 `SCHEDULER_ARM_RETURN_HOME_SETTLE_SEC=3.0` 秒给机械臂归位，再调用 `~/ros2/stop_reading_arm.sh` 停止 arm_agent/servo/driver 服务，最后恢复平台相机和安全守护。若测试失败，先保留终端日志，重点搜索 `[scheduler] event=...`、`[scheduler.arm] event=...`、`[scheduler.platform_camera] event=...` 三类日志。
如果看到 `auto_start_done ... health_ok=False`，先看前面的 `auto_start_wait_retry` 是否持续为 `frame_unavailable`；这表示读书相机服务启动了但 `/frame.jpg` 仍不可用，通常是 USB/相机占用或读书相机未出帧，而不是平台相机问题。

### CLI Debug: 手动复现读书/相机调度

当需要排查平台相机、机械臂相机、读书模式资源切换时，使用:

```bash
ssh elf@192.168.1.113
cd ~/cloud-model-safety-mainline
./scripts/start_system.sh --cli-debug
```

这个入口复用正式启动脚本的 ROS setup、平台相机启动、scheduler 环境变量和 safety/dashboard 初始化，但不会初始化 ASR/KWS/VAD、麦克风、LLM/VLM、TTS/扬声器。进入后用终端命令手动驱动:

```text
status          # 打印 scheduler/safety camera/arm 状态
start-platform  # 手动启动平台 Orbbec RGB/depth，相当于 scripts/start_platform_camera.sh
stop-platform   # 手动停止平台相机
enter-reading   # 复现 main.py 进入读书模式: start_reading(); mode=reading
page-done       # 复现 main.py 读完一页: pause_reading_page(); mode 仍为 reading，不恢复平台相机
next-page       # 复现 reading 模式继续下一页: start_reading()
exit-reading    # 复现退出/打断/空闲超时: stop_reading(return_home=True); mode=normal
arm-status      # 查询 arm_agent /book/status 与 /frame.jpg 健康
snapshot        # 保存当前平台 RGB 到 /tmp/debug_runtime_snapshot.jpg
quit            # 清理 safety/dashboard；若仍在 reading，会 return_home=True 后退出
```

推荐复现序列:

```text
status
enter-reading
status
page-done
status
next-page
page-done
exit-reading
status
quit
```

`page-done` 是专门复现正式 `main.py` 中“读完一页但仍留在读书模式”的路径；`next-page` 应打印 `reading_resources_reused`，且不应再次打印 `platform_camera_release_begin`。如果页间路径恢复平台相机慢，会看到 `[scheduler.platform_camera] event=start_begin/start_done` 之间有等待，这属于回归。调试失败时保留完整 CLI 输出。

手动启动仍可用:

```bash
ssh elf@192.168.1.113
bash /mnt/sdcard/reconstruct/fix_audio.sh   # 每次开机后
cd ~/cloud-model-safety-mainline
python3 main.py
```

## 核心能力

| 功能 | 状态 | 说明 |
|------|------|------|
| ASR 语音识别 | ✅ | SenseVoice + VAD 800ms 断句 |
| KWS 唤醒词/打断词 | ✅ | 唤醒: "你好小智""小智小智"; 打断: "停""停一下""停一停""别说""先别说" |
| VLM 多轮对话 | ✅ | qwen3-vl-flash，滑动窗口 + 压缩摘要 + 打断标记，timeout=30s |
| TTS 语音合成 | ✅ | qwen3-tts-instruct-flash-realtime (WebSocket)，7角色音色逐句切换，24kHz 实时流式，句间停顿 |
| Function Calling | ✅ | take_photo / get_brightness / get_motion / get_temperature |
| 故事模式 | ✅ | 关键词触发，临时换 prompt + max_tokens + speed，MODE="story" |
| **读书模式** | ✅ | 持续 OCR 朗读，MODE="reading"，退词/打断/空闲超时退出 |
| 固定语录 | ✅ | 唤醒/思考/拍照/读书/安全报警，走全局 `phrase_cache`，未命中时现场生成 WAV |
| **安全守护** | ✅ | 默认后台模块；摔倒 + 手接近尖锐物；VLM 二次复核；固定语录 TTS；事件落盘 |
| **家长看护后端** | ✅ | 默认后台 HTTP 服务；状态/环境/对话/安全/历史图像/睡眠设置 API |
| **打断机制** | ✅ | KWS 检测 → 杀播放 → 清队列 → drain mic → 等WAV → force_awake |
| **AEC 回声消除** | ✅ | WebRTC AudioProcessing，播放前喂参考信号，mic 经过滤再喂 KWS |
| 打断后恢复对话 | ✅ | 打断时 VLM 回复标记 `[此处对话被打断]`，保留对话历史 |

## MODE 状态机

```
MODE = "normal" | "reading" | "story"

normal ──(读书模式)──▶ reading ──(退出词/打断/5s空闲)──▶ normal
normal ──(讲故事)────▶ story   ──(完成/打断)───────────▶ normal
reading ──(续读)─────▶ reading (保持prompt/tools, 不下线)
```

## 家长看护仪表盘 API

默认启动:

```bash
cd ~/cloud-model
python3 main.py
```

`main.py` 会同时启动 `dashboard` 旁路 HTTP 线程:

```text
http://<板端IP>:8080
```

直接打开根路径会返回内置的 `parent-dashboard.html` 页面；API 仍在 `/api/...` 下。

前端 `parent-dashboard.html` 接真实板端时设置:

```javascript
BASE_URL: 'http://192.168.1.113:8080'
USE_MOCK: false
```

已实现接口:

```text
GET  /api/health
GET  /api/camera/snapshot
GET  /api/child/status
GET  /api/environment
GET  /api/conversation/summary
POST /api/message/send
POST /api/move
POST /api/move/find-child
POST /api/move/emergency-stop

GET  /api/reading/report
GET  /api/reading/records
GET  /api/activity
GET  /api/camera/history?date=YYYY-MM-DD
GET  /api/camera/history/image/{path}

GET  /api/safety/status
GET  /api/alerts
GET  /api/system/mode
GET  /api/system/resources
GET  /api/system/conflicts
GET  /api/system/features
GET  /api/sleep/status
GET  /api/sleep/children
POST /api/sleep/settings
POST /api/sleep/presence
POST /api/sleep/aid/start
POST /api/sleep/aid/stop
POST /api/sleep/remind
```

数据来源:

- 安全告警与历史图像读取 `~/cloud-model/safety_records/index.jsonl` 和事件目录。
- 读书历史图像读取 `reading_captures/`；每次读书拍摄保存 raw/vlm 图和 JSON 元数据。
  JSON 中的 `book_detection` 来自 `book_detect_infer()`，包含每页角点时会随 `/api/camera/history`
  作为 `book_pages` 返回并由前端 overlay 显示。
- 实时摄像头快照走安全守护缓存的平台 ROS RGB 帧；读书机械臂相机只在读书链路使用。
- 系统 mode/resource/conflict/features 读取 `runtime_scheduler` 和 dashboard adapter 快照，不启动或停止进程。
- 家长发送消息和睡眠提醒进入主循环队列，由现有 TTS 播放。
- 助眠开始/停止目前只管理 dashboard 计时和睡眠状态，不播放白噪音/音乐/故事音频；
  前端会根据 `/api/sleep/aid/start` 返回的剩余时间本地读秒，到期自动复位，停止后立即应用 `/api/sleep/aid/stop` 返回状态。
- 移动、找孩子、急停进入 `dashboard/chassis_control.py`。`./scripts/start_system.sh`
  默认启用 `DASHBOARD_CHASSIS_CONTROL_ENABLED=1`，并由 main 调用
  `person_task_controller.ensure_chassis_support_stack()` 拉起底盘安全链路。
  移动和急停会发布 `/cmd_vel_raw` 意图。
  默认线速度/角速度对齐 `teleop_twist_keyboard` 的 `speed=0.5`、`turn=1.0`。
  前端方向键按住期间每 150ms 重发一次，避免 obstacle_guard `cmd_timeout_s` 过期；松开后发 stop。
  实际车动还要求 `depth_obstacle_guard` 订阅 `/cmd_vel_raw`、发布 `/cmd_vel`，且 `driver_node` 订阅 `/cmd_vel`。
  读书/人物任务运行时，手动方向键会被禁用并由后端 busy 保护；急停仍可用。
  页面里显示的“实时通信”是预留 WebSocket/SSE 能力，不是当前底盘控制的必需条件。
- 睡眠状态根据 bedtime、children、grace_minutes、最近 `/api/sleep/presence` 上报的可见孩子计算。

配置:

```bash
DASHBOARD_ENABLED=0          # 临时关闭家长端后端
DASHBOARD_HOST=0.0.0.0
DASHBOARD_PORT=8080
DASHBOARD_SAFETY_ACTIVE_SEC=90
DASHBOARD_ALERT_WINDOW_SEC=900
DASHBOARD_CHASSIS_CONTROL_ENABLED=1  # 默认启用；设 0 可关闭 dashboard 底盘发布和支持栈启动
DASHBOARD_CHASSIS_CMD_VEL_RAW_TOPIC=/cmd_vel_raw
DASHBOARD_CHASSIS_LINEAR_MPS=0.5
DASHBOARD_CHASSIS_ANGULAR_RADPS=1.0
RESOURCE_SCHEDULER_ENABLED=1
SCHEDULER_AUTO_START_READING_ARM=1
SCHEDULER_READING_PAUSES_SAFETY=1
SCHEDULER_REQUIRE_FRAME_HEALTH=1
ASR_KWS_PROGRESS_EVERY=0     # 默认关闭 KWS 周期进度日志；设为 50 可同行刷新诊断
```

## 当前工作流

```
SLEEP ──(唤醒词)──▶ AWAKE ──(VAD静音)──▶ SLEEP(处理中) ──(播放完毕)──▶ AWAKE
  ▲  KWS活跃           VAD+ASR活跃         KWS活跃=打断监听       │
  │                    │                   │                      │
  │                    │               (打断词检测)            (5s空闲)
  │                    │                   │                      │
  │                    └─── 清空一切 ◀──────┘                      │
  │                speaker.cancel + drain_mic + clear ASR queue   │
  │                + reading_out_filler + speaker.wait            │
  │                + force_awake (在 process_utterance 中等WAV)    │
  └────────────────────────────────────────────────────────────────┘
```

## 架构要点

### 线程模型
- **主线程**: 永不阻塞的 mic 泵 (mic.read → AEC → asr.feed(bytes))，检查 ASR 结果/打断/自动唤醒/空闲
- **_run 线程**: ASR 后台处理 (KWS 或 VAD+ASR)，所有 VAD/KWS 状态变更在此线程执行
- **处理线程**: 后台执行 VLM + TTS + speaker.wait()
- **合成线程**: RealtimeSpeaker 内部，WebSocket TTS 会话 (switch_voice 逐句切换音色)
- **播放线程**: RealtimeSpeaker 内部，aplay -D DEVICE_SPK stdin 喂 24kHz PCM + AEC 参考信号喂入
- **安全线程**: `SafetyMonitor` 按 target Hz 拉 ROS RGB 最新帧并跑 RKNN；读书模式可通过 `pause("reading")` 跳过 RKNN；`safety-analyzer` 对候选事件做 VLM 复核、记录和播报
- **调度器**: `RuntimeCoordinator` 在 main.py 中启动，维护 normal/reading 资源租约，包装 `arm_agent` 健康检查、读书 start/stop、安全 pause/resume 和 dashboard 状态快照

### VAD 防护
- **唤醒**: request_state(True) → 异步 wake_reply()；不等待固定语录播完，也不清空 ASR 队列，避免吞掉用户紧接着说的问题开头。
- **打断**: speaker.cancel → drain_mic → 清 ASR 队列 → force_awake (在 process_utterance 中等 WAV 播完)
- **读书退出**: reading_out_filler() → speaker.wait() → force_awake
- 除唤醒语录外，固定语录播放后再清队列/开 VAD，防止扬声器回声被录入；唤醒语录必须异步播放，避免丢用户抢答开头。

### 分支边界
- 安全守护集成内容已于 2026-06-27 合入 `master`；历史开发分支为 `feat/safety-guard-mainline`。
- ESP32 watch camera demo 不属于本分支；不要把 `vision/watch_camera.py` 或对应测试从 `codex/test-esp32-take-photo` 合入本分支。

### 资源调度
- 已知冲突包括 USB/V4L2 摄像头与 Orbbec/USB 音频/机械臂串口带宽、读书/安全/寻人 RKNN NPU 竞争、TTS 播报抢占、ROS 图像多消费者带来的 CPU/JPEG 压力。
- 第一版调度器已放在 `runtime_scheduler/`，由 `cloud-model` 作为策略和模式 owner；`~/ros2` 继续作为执行层，通过现有 `arm_agent` HTTP 接口被包装。
- 相机命名边界:
  - `ROS_RGB_CAMERA` / `ROS_DEPTH_CAMERA` 指平台深度/RGB 相机 topic。
  - `USB_V4L2_CAMERA` 指机械臂读书相机的直接 V4L2 reader。
  - 读书模式暂停 safety RKNN 推理，并默认停止平台相机 publisher 释放 USB 带宽。
- `main.py` 默认创建 `RuntimeCoordinator`。进入读书模式时会先停平台相机，再按需启动/检查 `arm_agent` `/book/status` 和 `/frame.jpg`，随后调用 `/reading/prepare` 与 `/reading/start`；失败会恢复 normal、重启平台相机并恢复安全守护。
- dashboard 新增 `/api/system/mode`、`/api/system/resources`、`/api/system/conflicts`、`/api/system/features` 只读接口。
- NPU 调度层已拆出 `npu_core_0`、`npu_core_1`、`npu_core_2`。旧 `NPU_SAFETY/NPU_BOOK/NPU_PERSON_FACE`
  逻辑资源保留兼容；新 mode policy 优先使用物理 core 资源，便于后续把 RKNN runtime 绑定 core mask。
- 详细 branch 修改计划见 `docs/superpowers/plans/2026-06-21-resource-scheduler-branch-plan.md`。保持 `RESOURCE_SCHEDULER_ENABLED=0` 回滚路径，不引入 ESP32/watch-camera 代码。

### 线程安全
- VAD/KWS 状态变更通过 `_pending_state` + `_force_reset` 标志延迟到 `_run` 线程执行
- `SharedAecFilter` 用 `threading.Lock` 保护 WebRTC 对象
- `RealtimeSpeaker.cancel()` 用 `_player_lock` 保护 aplay 进程句柄
- 摄像头 `capture()` 用 `threading.Lock` 保护持久 VideoCapture 对象

### 关键参数
- `CHUNK_BYTES=320` (160 samples = 10ms @ 16kHz)
- `AEC_FRAME_SAMPLES=160` (与 mic chunk 对齐)
- `IDLE_SLEEP_SEC=5`
- VAD: silero `threshold=0.7`, `min_silence=800ms`
- KWS: `keywords_threshold=0.1, keywords_score=3.0`
- ASR: `num_threads=2`, `VAD num_threads=1`, `KWS num_threads=1`
- ASR queue: `Queue(maxsize=300)` + `put_nowait()` (丢弃策略防 OOM)
- VLM API: `timeout=30`
- TTS 实时: 输出 24kHz, 句间静音 200ms, commit 超时 30s, WebSocket 重连 3 次
- 摄像头: 持久连接, 首次预热 ~2s, 后续 ~10ms 取帧, 每次拍照前清 5 帧缓冲区

## 注意事项

- API Key 不要提交到 Git
- SD 卡通过 fstab 固定挂载 `/mnt/sdcard`，软链接 `/home/elf/Desktop/reconstruct` → `/mnt/sdcard/reconstruct`
- 旧 SD 卡挂载目录残留会导致 udisks2 加"1"后缀，需手动清理 `/media/elf/` 下空目录
- KWS 模型阈值 `keywords_threshold=0.1` 较低（敏感），可能偶尔误触发
- AEC 依赖 `libwebrtc-audio-processing1` (apt 安装)
- TTS 音色切换通过 LLM 输出 `[VoiceName]` 标记实现，`feed()` 检测并剥离，大小写不敏感；实时 TTS 中音色切换为断连重连（API 不支持 mid-session update_session）
- 所有 system prompt 末尾注明禁止 Markdown/emoji，`feed()` 同时过滤 `*#_~` ` 等格式符号
- 打断后对话历史保留，打断的回复标记 `[此处对话被打断]`
- 打断成功率受声学环境影响（喇叭音量、mic 距离、房间回声）
- 备份文件在 `backup/` 目录
- 板端 Git 仓库: `~/cloud-model` (master 分支), Windows 仓库: `D:\嵌赛项目\云端模型`
