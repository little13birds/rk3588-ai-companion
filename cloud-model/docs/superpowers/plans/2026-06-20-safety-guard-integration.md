# Safety Guard × cloud-model — 集成规划

日期: 2026-06-20  
分支: `feat/safety-guard-integration`  
状态: 规划中，待实现  
当前基础分支来源: `codex/test-esp32-take-photo @ 2761e6d`

## 背景

`cloud-model` 目前是语音助手主进程，主循环负责:

- 麦克风输入: `arecord -> AEC -> ASR.feed`
- ASR/KWS/VAD: `ASRProcessor`
- LLM/VLM: `Conversation`
- TTS: `RealtimeSpeaker`
- 读书模式、故事模式、机械臂读书跟踪

`safety_guard_rk3588` 目前是独立安全守护工程，已经在板端验证:

- ROS RGB topic: `/camera/color/image_raw`
- RKNN shared library: `~/safety_guard_rk3588/board/build/libsafety_rknn.so`
- 模型:
  - `pose_yolov8n_hybrid.rknn`
  - `hand_yolov8n_int8.rknn`
  - `hazard_yolov8s_coco_int8.rknn`
- C API:
  - `safety_rknn_create`
  - `safety_rknn_set_config`
  - `safety_rknn_process_bgr`
  - `safety_rknn_destroy`
- 现有 Python 测试服务已验证 5 FPS 主循环、hand/hazard 200 ms 更新。

本分支目标是把安全守护作为 `cloud-model` 启动时的后台模块加载，而不是再由独立 8092 服务常驻。

因此需要重新划分 C++/动态库/模型的归属:

- C++ 源码和构建脚本必须纳入 `cloud-model` 分支，避免 Python 集成长期依赖外部临时目录。
- 编译出的 `libsafety_rknn.so` 是板端构建产物，默认不作为普通源码提交；运行时可以由构建脚本生成，也可以从指定路径加载。
- `.rknn` 模型是大体积运行产物，默认不提交进 git；通过配置路径引用，板端部署时放在固定目录。

## 目标

1. `cloud-model/main.py` 启动时同时启动安全守护模块。
2. 安全监测频率必须可配置，至少提供 `target_frequency_hz` 运行时接口。
3. 当安全守护检测到以下候选事件时:
   - 摔倒: `fall_active=true`
   - 手接近/拿起危险物品: `hand_hazard_active=true`
4. 使用新的、独立的 prompt/context 向 VLM 提交图像和安全检测元数据，不污染普通对话上下文。
5. VLM 判断有危险:
   - 记录危险事件数据和图像。
   - TTS 播报警告，必要时提醒儿童停止危险动作。
6. VLM 判断不属于危险:
   - 记录为疑似事件。
   - 不播报。
7. 数据记录格式为后续家长端/前端查询历史留好稳定接口。

## 非目标

- 本阶段不训练新模型。
- 本阶段不改 RKNN 后处理算法，优先原样迁移已验证的 C++ shared library 源码到 `cloud-model`。
- 本阶段不实现完整家长前端，只落地可查询的数据文件结构和索引。
- 本阶段不把安全事件写入普通聊天历史，避免污染儿童对话上下文。
- 本阶段不复用 8092 独立网页服务；集成后应避免同时运行独立 safety server 和 cloud-model 安全模块，防止重复占用 NPU/CPU。

## 建议架构

新增 `safety_guard/` 包，安全逻辑从 `main.py` 中隔离出来。

```text
cloud-model/
  safety_guard_native/
    CMakeLists.txt       # 只构建 libsafety_rknn.so
    rknn_yolo.h
    rknn_yolo.cpp
    safety_rknn_lib.h
    safety_rknn_lib.cpp
    build_native.sh      # 板端构建脚本
    README.md

  safety_guard/
    __init__.py
    config.py          # SafetyGuardConfig dataclass + env/defaults
    rknn_runtime.py    # ctypes 封装 libsafety_rknn.so
    ros_camera.py      # ROS RGB 订阅，取 latest BGR frame
    monitor.py         # 后台监测线程，频率控制，事件去抖
    analyzer.py        # 独立 VLM 安全判断
    recorder.py        # 事件落盘、索引 JSONL
    announcer.py       # TTS 安全播报协调
    prompts.py         # 安全分析 prompt 与输出 schema
```

主进程只接入一个窄接口:

```python
from safety_guard import SafetyGuardService, SafetyGuardConfig

safety_guard = SafetyGuardService(
    config=SafetyGuardConfig.from_env(),
    speaker=speaker,
    cancel_event=cancel_event,
    eye_state=eye_state,
)
safety_guard.start()
...
safety_guard.stop()
```

`main.py` 里不直接写 ROS/RKNN/VLM 细节。

## Native runtime 归属策略

### 现有外部 native 依赖参考: 书本系统

当前 `cloud-model` 已经有一套外部 native/模型依赖，书本监测和书本数据库并没有完整纳入本仓库:

- `book_match_client.py` 在 `cloud-model` 内，但只是 ctypes 包装层。
- `BookMatchClient` 默认加载:
  - `~/book_detect/build/libbook_match.so`
  - `~/book_detect/build/libbook_detect.so`
  - `~/book_detect/model/best_hybrid_v9.rknn`
- 书本数据库与 CLIP/ONNX 模型来自:
  - `~/Desktop/database_tokenize_match/database`
  - `~/Desktop/database_tokenize_match/models`
- `~/book_detect` 才包含当前实际使用的 `book_detect.cpp/.h`、`book_match.cpp/.h`、CMake 和动态库构建产物。
- `~/ros2/src/face_track` 包含书本/机械臂监测服务 `arm_agent.py`，`cloud-model/arm/agent_client.py` 只是通过 HTTP 调用 `127.0.0.1:8642`。
- 2026-06-20 检查读书模式机械臂不动时发现 `/dev/video21` 与 Orbbec 同时运行会触发 `uvcvideo Failed to submit URB 0 (-28)`，arm_agent 已改为默认订阅 Orbbec RGB `/camera/color/image_raw`，V4L2 只保留为参数化回退。

这说明 `cloud-model` 目前并不是书本系统源码的唯一仓库，而是运行编排层。安全模块如果继续只依赖 `~/safety_guard_rk3588`，会形成类似的外部漂浮依赖。为了可维护性，本次 safety 相关 C++ source 应直接迁入 `cloud-model/safety_guard_native/`，而模型和 `.so` 按可复现构建产物管理。

### C++ 源码

需要放入 `cloud-model/safety_guard_native/` 并纳入 git:

- `rknn_yolo.h`
- `rknn_yolo.cpp`
- `safety_rknn_lib.h`
- `safety_rknn_lib.cpp`
- `CMakeLists.txt`
- `build_native.sh`
- `README.md`

不建议迁移:

- `full_safety.cpp`: 旧的独立摄像头/HTTP 服务，不应混入 cloud-model 主进程。
- `eval_images.cpp`: 离线评估工具，可后续需要时再迁移。
- `httplib.h`: 集成后不在 native 层开 HTTP，不需要。

### 动态库

运行时需要 `libsafety_rknn.so`，但它是构建产物。

建议路径:

```text
cloud-model/
  safety_guard_native/build/libsafety_rknn.so
```

`.gitignore` 应增加:

```text
safety_guard_native/build/
safety_guard_native/*.so
safety_guard/models/
safety_records/
```

原因:

- `.so` 与 RK3588/RKNN runtime/OpenCV ABI 绑定，跨机器不可用。
- git 中保存源码和构建脚本即可复现。
- 板端比赛运行可保留 `.so` 文件在 build 目录，不提交也不影响运行。

`SafetyGuardConfig` 默认查找顺序:

1. `SAFETY_GUARD_LIB` 环境变量。
2. `~/cloud-model/safety_guard_native/build/libsafety_rknn.so`。
3. 兼容旧路径 `~/safety_guard_rk3588/board/build/libsafety_rknn.so`。

### RKNN 模型

模型默认不提交进 git，但需要稳定运行路径。

建议路径:

```text
cloud-model/
  safety_guard/models/
    pose_yolov8n_hybrid.rknn
    hand_yolov8n_int8.rknn
    hazard_yolov8s_coco_int8.rknn
```

`SafetyGuardConfig` 默认查找顺序:

1. `SAFETY_GUARD_MODEL_DIR` 环境变量。
2. `~/cloud-model/safety_guard/models/`。
3. 兼容旧路径 `~/safety_guard_rk3588/model/`。

后续部署脚本可以从 `~/safety_guard_rk3588/model/` 复制模型到 `~/cloud-model/safety_guard/models/`，但不把 `.rknn` 纳入 git。

## 线程模型

```text
main.py 主线程:
  mic.read -> AEC -> ASR.feed
  处理 ASR result/打断/休眠
  启动/停止 SafetyGuardService

SafetyGuardService:
  ros spin thread:
    订阅 /camera/color/image_raw，只保存 latest frame

  monitor thread:
    按 target_frequency_hz 取 latest frame
    调用 RKNN C API
    解析 safety JSON
    对 fall/hand_hazard 候选事件做去抖和冷却
    生成 SafetyCandidate

  analyzer worker:
    候选事件 -> 独立 VLM prompt + 图像
    输出 SafetyAnalysis JSON

  recorder:
    所有候选事件都落盘
    confirmed danger 和 suspected 分开标记

  announcer:
    仅 confirmed danger 调 TTS
    高危事件可打断当前普通播报
```

## 频率接口

需要同时支持配置文件/环境变量和运行时接口。

### 配置项

```python
class SafetyGuardConfig:
    enabled: bool = True
    target_frequency_hz: float = 20.0
    hazard_period_sec: float = 0.2
    rgb_topic: str = "/camera/color/image_raw"
    native_lib_path: str = ""
    model_dir: str = ""
    event_cooldown_sec: float = 8.0
    analyzer_max_pending: int = 3
    record_dir: str = "~/cloud-model/safety_records"
```

环境变量:

```bash
# 默认启用；临时关闭时设置 SAFETY_GUARD_ENABLED=0
SAFETY_GUARD_TARGET_HZ=20
SAFETY_GUARD_HAZARD_PERIOD=0.2
SAFETY_GUARD_LIB=~/cloud-model/safety_guard_native/build/libsafety_rknn.so
SAFETY_GUARD_MODEL_DIR=~/cloud-model/safety_guard/models
SAFETY_GUARD_EVENT_COOLDOWN=8
```

### 运行时接口

```python
service.set_target_frequency_hz(5.0)
service.get_target_frequency_hz()
```

后续若要接前端，可以在 cloud-model 内新增轻量 HTTP API:

```text
GET  /safety/config
POST /safety/config {"target_frequency_hz": 3.0}
GET  /safety/events
GET  /safety/events/{event_id}
```

本阶段先实现 Python 内部接口和文件记录，不强制开 HTTP。

## 候选事件判定

RKNN JSON 中已有:

```json
{
  "fall_active": false,
  "hand_hazard_active": false,
  "counts": {"persons": 0, "hands": 0, "hazards": 0, "relations": 0},
  "tracks": [],
  "hazards": [],
  "hands": [],
  "relations": []
}
```

候选事件规则:

- `fall_candidate`: `fall_active == true`
- `hazard_candidate`: `hand_hazard_active == true`
- `combined_candidate`: 两者同时为 true

去抖策略:

- 对同一事件类型设置 `event_cooldown_sec`，默认 8 秒。
- 活跃状态持续期间不要每 200 ms 都请求 VLM。
- 事件从 false -> true 时立刻触发一次。
- 若持续 active，超过 cooldown 后可以再次触发，用于持续危险提醒。

## VLM 二次判断

必须使用独立 context，不用 `Conversation.messages`。

建议新增:

```python
class SafetyRiskAnalyzer:
    def analyze(candidate: SafetyCandidate) -> SafetyAnalysis:
        ...
```

调用 OpenAI-compatible client:

```python
client.chat.completions.create(
    model=LLM_MODEL,
    messages=[
        {"role": "system", "content": SAFETY_ANALYSIS_SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},
            {"type": "text", "text": "...检测元数据 JSON..."}
        ]},
    ],
    stream=False,
    max_tokens=220,
    timeout=15,
)
```

### 安全分析输出格式

要求模型只输出 JSON。解析失败则按 `suspected` 记录，不播报。

```json
{
  "danger": true,
  "risk_type": "fall|sharp_object|unknown",
  "severity": "low|medium|high|critical",
  "summary": "孩子手里疑似拿着剪刀，尖端朝外。",
  "tts": "危险，请马上放下剪刀，远离尖锐物品。",
  "evidence": ["检测到 hand_hazard_active", "图像中手与剪刀接触"],
  "recommended_action": "家长应立即查看现场"
}
```

### Prompt 原则

- 明确这只是安全复核，不要聊天。
- 模型必须保守判断: 看不清时 `danger=false` 但 `severity=low`，作为 suspected 保存。
- 对儿童安全事件，若存在明显摔倒、持刀、持剪刀、手接近危险物体，输出 `danger=true`。
- TTS 文案必须短、直接、适合现场播放，避免恐吓。

## 图像选择

安全模块应保存两张图:

- `raw.jpg`: 原始 RGB 帧压缩后的图，给 VLM 主要分析。
- `annotated.jpg`: RKNN 画框图，给调试和家长端查看。

当前 C API 返回的是 `annotated.jpg` 和 JSON。`monitor.py` 同时持有 ROS latest raw BGR，可自行压缩出 `raw.jpg`。

发送给 VLM 的建议:

- 第一阶段只发送 `raw.jpg` + 检测元数据。
- 如果误判较多，再考虑同时发送 annotated 图或把框坐标写入文本。

## 事件记录格式

根目录:

```text
~/cloud-model/safety_records/
  index.jsonl
  2026-06-20/
    20260620T153012_abc123/
      event.json
      raw.jpg
      annotated.jpg
      rknn_status.json
      vlm_response.json
```

`event.json`:

```json
{
  "event_id": "20260620T153012_abc123",
  "created_at": "2026-06-20T15:30:12+08:00",
  "candidate_type": "hazard_candidate",
  "confirmed": true,
  "severity": "high",
  "risk_type": "sharp_object",
  "summary": "孩子疑似拿起剪刀。",
  "tts": "危险，请马上放下剪刀，远离尖锐物品。",
  "paths": {
    "raw": "2026-06-20/20260620T153012_abc123/raw.jpg",
    "annotated": "2026-06-20/20260620T153012_abc123/annotated.jpg"
  },
  "rknn": {},
  "vlm": {}
}
```

`index.jsonl` 每行保存一个事件摘要，方便前端分页读取。

## TTS 播报策略

危险事件应能打断普通回答，但要避免和现有 TTS 队列竞争。

建议实现 `SafetyAnnouncer`:

```python
class SafetyAnnouncer:
    def announce(text: str, severity: str):
        if severity in {"high", "critical"}:
            cancel_event.set()
            speaker.cancel()
            speaker.reset()
        speaker.feed(text)
        speaker.flush()
        speaker.wait()
```

实现时需要注意:

- `RealtimeSpeaker` 当前可能被普通 `Conversation.ask()` 同时使用。
- 高危安全播报优先级高于普通对话。
- 若普通处理线程被安全播报打断，应复用现有 `cancel_event` 路径，让普通回答自然停止并标记中断。
- 建议把播报放到单独线程，避免阻塞监测循环。

后续更稳的方案是抽象统一的 `AudioOutputController`，让普通对话和安全播报都通过同一优先级队列输出。本阶段先做最小可行集成。

## 与现有 main.py 的集成点

启动阶段:

```python
speaker = StreamSpeaker(...)
...
safety_guard = SafetyGuardService(..., speaker=speaker, cancel_event=cancel_event)
safety_guard.start()
```

退出阶段:

```python
finally:
    safety_guard.stop()
    asr.stop()
    mic.terminate()
```

共享状态:

- `speaker`: 用于播报危险。
- `cancel_event`: 高危事件打断普通 TTS/LLM。
- `eye_state`: 可选，高危事件时切到 `thinking` 或眨眼。
- 不直接访问 `conv.messages`。

## 实现任务

### Task 0: 迁移 native source 和构建脚本

Files:

- Create: `safety_guard_native/CMakeLists.txt`
- Create: `safety_guard_native/rknn_yolo.h`
- Create: `safety_guard_native/rknn_yolo.cpp`
- Create: `safety_guard_native/safety_rknn_lib.h`
- Create: `safety_guard_native/safety_rknn_lib.cpp`
- Create: `safety_guard_native/build_native.sh`
- Create: `safety_guard_native/README.md`
- Modify: `.gitignore`

来源:

- `~/safety_guard_rk3588/board/rknn_yolo.*`
- `~/safety_guard_rk3588/board/safety_rknn_lib.*`
- `~/safety_guard_rk3588/board/CMakeLists.txt`

要求:

- 只构建 `libsafety_rknn.so`，不构建旧 HTTP/camera21 程序。
- `RKNN_INCLUDE` 保持可配置，默认兼容当前板端 SDK 路径。
- build 目录和 `.so` 不提交。

验收:

```bash
cd ~/cloud-model/safety_guard_native
./build_native.sh
test -f build/libsafety_rknn.so
```

### Task 1: 建立 safety_guard 包骨架

Files:

- Create: `safety_guard/__init__.py`
- Create: `safety_guard/config.py`
- Create: `safety_guard/prompts.py`
- Create: `safety_guard/types.py`

验收:

- `python3 -m py_compile safety_guard/*.py` 通过。
- `SafetyGuardConfig.from_env()` 可读取目标频率。

### Task 2: 抽出 RKNN 和 ROS 运行时

Files:

- Create: `safety_guard/rknn_runtime.py`
- Create: `safety_guard/ros_camera.py`

来源:

- 参考 `~/safety_guard_rk3588/ros_tools/ros_safety_guard_server.py`
- 不复制 HTTP server 逻辑。
- `rknn_runtime.py` 默认加载 `~/cloud-model/safety_guard_native/build/libsafety_rknn.so`。
- 模型默认从 `~/cloud-model/safety_guard/models/` 加载，缺失时兼容旧路径 `~/safety_guard_rk3588/model/`。

验收:

- 可以在 cloud-model 进程中初始化 `libsafety_rknn.so`。
- 可以订阅 `/camera/color/image_raw` 并取 latest BGR。
- `process(frame)` 返回 raw/annotated/status。

### Task 3: 监测循环和频率接口

Files:

- Create: `safety_guard/monitor.py`

功能:

- `start()`
- `stop()`
- `set_target_frequency_hz(hz)`
- `get_target_frequency_hz()`
- 事件去抖和 cooldown。

验收:

- `target_frequency_hz=5` 时 `hazard_age_sec` 应接近 0.2 秒。
- 不因没有 ROS 图像而阻塞 main loop。

### Task 4: 独立 VLM 安全判断

Files:

- Create: `safety_guard/analyzer.py`

功能:

- 使用独立 messages，不污染 `Conversation`。
- 输出严格 JSON。
- 解析失败保存为 suspected，不播报。

验收:

- 可用一张本地图像和 mock RKNN metadata 生成 `SafetyAnalysis`。
- danger=true/false 两类都能处理。

### Task 5: 事件记录

Files:

- Create: `safety_guard/recorder.py`

功能:

- 保存 raw/annotated/status/vlm/event。
- 追加 `index.jsonl`。
- 区分 confirmed 和 suspected。

验收:

- 每个候选事件都有完整目录。
- confirmed danger 和 suspected 都可追踪。

### Task 6: TTS 安全播报

Files:

- Create: `safety_guard/announcer.py`
- Modify: `main.py`

功能:

- confirmed danger 调 TTS。
- high/critical 可 `cancel_event.set()` 并 `speaker.cancel()`。
- suspected 不播报。

验收:

- 安全播报不会让 main.py 崩溃。
- 普通对话被安全事件打断后可恢复监听。

### Task 7: main.py 接入

Files:

- Modify: `main.py`

要求:

- 启动时加载安全守护。
- 安全模块初始化失败时给出日志，可配置是否让程序继续运行。
- finally 中停止安全模块。

验收:

- `python3 main.py` 启动后同时有 ASR 主循环和 safety monitor。
- 退出时线程清理干净。

### Task 8: 文档和运行方式

Files:

- Update: `CLAUDE.md`
- Update: this plan

运行方式建议:

```bash
source ~/ros2/install/setup.bash
export ROS_DOMAIN_ID=30
export SAFETY_GUARD_TARGET_HZ=20
cd ~/cloud-model
python3 main.py
```

## 风险与决策点

1. ROS 环境依赖:
   - 如果不 source `~/ros2/install/setup.bash`，`rclpy/cv_bridge` 可能不可用。
   - 方案: safety module 初始化失败时禁用自身并打印明确日志；比赛运行脚本负责 source ROS。

2. NPU/CPU 资源:
   - cloud-model 集成版运行时不要同时开 `~/safety_guard_rk3588` 的 8092 服务。
   - 方案: 文档和启动脚本中明确二选一。

3. TTS 并发:
   - 安全播报和普通回答共用 `RealtimeSpeaker`，存在并发竞争。
   - 方案: 高危事件走 `cancel_event + speaker.cancel()`，后续再升级为统一音频优先级队列。

4. VLM 延迟:
   - 每个候选事件都请求 VLM 会有延迟和费用。
   - 方案: cooldown、队列限长、只在 false->true 或 cooldown 到期时分析。

5. 误检:
   - 目前 hazard 模型是 COCO 预训练低阈值，误检会多。
   - 方案: RKNN 只做候选，VLM 做二次确认；suspected 全记录但不播报。

## 当前 Git 注意事项

历史记录：`feat/safety-guard-mainline` 曾从 `master` 派生用于安全守护集成；该内容已于 2026-06-27 合入 `master`。

明确不纳入:

- ESP32 watch camera demo
- `vision/watch_camera.py`
- `vision/test_watch_camera.py`
- eyes integration 分支改动

后续如需处理 ESP32 取图，应在单独分支中完成，确认稳定后再决定是否合入主线。

## 2026-06-20 实施进度

已完成:

- 新增 `safety_guard_native/`，把 safety RKNN C++ wrapper 和 YOLO 后处理源码纳入 `cloud-model`。
- 新增 `safety_guard/` Python 包，包含配置、ROS RGB 订阅、RKNN runtime、监测循环、VLM 复核、事件记录、TTS 播报封装。
- 修改 `main.py`，在主程序启动 ASR/mic 后启动 `SafetyGuardService`，在 `finally` 中停止。
- `.gitignore` 已忽略 native build 产物、`.so`、模型目录和安全事件记录目录。
- `SafetyGuardConfig.enabled` 默认启用；如需临时关闭安全守护，启动前设置 `SAFETY_GUARD_ENABLED=0`。

板端验证:

```text
cd ~/cloud-model
python3 -m py_compile main.py safety_guard/*.py

cd ~/cloud-model/safety_guard_native
./build_native.sh
=> built: /home/elf/cloud-model/safety_guard_native/build/libsafety_rknn.so
```

关闭态验证:

```text
SAFETY_GUARD_ENABLED=0
start_ret False
hz_after 9.0
lib /home/elf/cloud-model/safety_guard_native/build/libsafety_rknn.so
model_dir /home/elf/safety_guard_rk3588/model
```

启动态短跑验证:

```text
SAFETY_GUARD_ENABLED=1
SAFETY_GUARD_TARGET_HZ=2
SAFETY_GUARD_HAZARD_PERIOD=0.5

[safety] event=started component=service target_hz=2.00 hazard_period=0.50s ...
hz_before 2.0
hz_after 3.5
pose/hand/hazard 三个 RKNN 模型均加载成功
```

当前限制:

- SSH 环境里 `ros2 topic list` 只看到 `/parameter_events` 和 `/rosout`，没有 RGB 图像话题。
- 因此本次只验证到 ROS node/RKNN 初始化和频率接口，没有验证实际帧推理、候选事件、VLM 复核、TTS 播报链路。
- 下一次需要在确认 RGB topic 可见后，用真实相机帧验证 `camera_stats.received > 0`、候选事件记录和播报。

## 2026-06-20 现场测试记录

已验证:

- `fall_candidate` 可触发 VLM 复核、事件记录和 TTS 播报。
- `hazard_candidate` 可触发 VLM 复核、事件记录和 TTS 播报。
- 事件记录目录已生成 `raw.jpg`、`annotated.jpg`、`rknn_status.json`、`vlm_response.json`、`event.json`，并写入 `safety_records/index.jsonl`。

修正:

- VLM 曾生成 `小心地滑，快站起来！`，其中 `地滑` 容易被 TTS 读错，且 `快站起来` 对摔倒场景不够安全。
- 已在 prompt 中禁止 `地滑/小心地滑/快站起来` 类表达。
- 已在 `SafetyAnnouncer` 播报前增加兜底归一化: 摔倒类如果含有 `地滑/快站/站起来/爬起来`，改播 `检测到可能摔倒了，请先不要乱动，等待大人帮助。`

## 2026-06-20 频率调整

- 姿态检测 safety target 已从默认 `5Hz` 调整为 `20Hz`。
- `SafetyGuardConfig.clamp()`、`SafetyMonitor.set_target_frequency_hz()`、`SafetyGuardService.set_target_frequency_hz()` 上限同步从 `15Hz` 调整为 `20Hz`。
- 危险品检测仍由 `SAFETY_GUARD_HAZARD_PERIOD` 控制；默认 `0.2s`，即 hand/hazard 约 `5Hz`，不会随 pose target 自动升到 `20Hz`。

## 2026-06-20 固定语录缓存池

新增全局固定语录缓存:

- `tts/phrase_cache.py`
- 默认缓存目录: `audio/generated_phrases/`
- 可用 `TTS_PHRASE_CACHE_DIR` 覆盖缓存目录。
- 缓存 key 包含 `phrase_id/text/voice/model/instructions/sample_rate`，TTS 模型或参数变化后会自动生成新 WAV。
- `audio/generated_phrases/` 已加入 `.gitignore`，现场生成的音频不进入源码提交。

新增播放接口:

```python
speaker.queue_phrase(
    phrase_id="safety.fall.01",
    text="检测到你可能摔倒了，别害怕，请先不要乱动，等大人来帮忙。",
    voice="Cherry",
)
```

行为:

- 缓存命中: 直接 `queue_wav()` 播放。
- 缓存未命中: 使用当前 `qwen3-tts-instruct-flash-realtime` 参数现场生成 WAV，写入缓存后播放。
- 生成失败: fallback 到旧 WAV 或直接走实时 TTS 文本合成。

已迁移:

- `audio/fillers.py` 的 wake/think/photo/reading 固定语录。
- `safety_guard` 的摔倒、剪刀、刀具、叉子、通用尖锐物、组合风险播报。

安全播报策略:

- VLM 仍负责判断 `danger/risk_type/severity/summary/evidence`。
- 现场 TTS 不再直接使用 VLM 动态生成的 `tts`。
- `SafetyAnnouncer.prepare_phrase()` 会把 `analysis.tts` 改成固定语录，并写入 `tts_phrase_id/tts_source`，事件记录中保存实际播报文本。

重复播报修复:

- `SafetyMonitor` 候选去重从 `fall_candidate/hazard_candidate/combined_candidate` 改为风险族 `fall/hazard`，避免 `fall_candidate` 与 `combined_candidate` 切换时立即重复提交同类摔倒事件。
- 新增 `SAFETY_GUARD_ANNOUNCE_COOLDOWN`，默认 `20s`。
- `SafetyAnnouncer` 对同类播报 key 做最终冷却，例如 `fall`、`sharp:scissors`、`combined`，防止同一次摔倒短时间多次播报。

## 2026-06-20 默认启动调整

- 安全守护模块改为随 `cloud-model` 默认启动，不再要求显式设置 `SAFETY_GUARD_ENABLED=1`。
- 保留 `SAFETY_GUARD_ENABLED=0` 作为临时关闭开关。
- 默认配置仍为 fail-open: 如果 ROS/RKNN/模型路径初始化失败，安全模块会打印错误并自我禁用，不阻塞语音主程序。

推荐启动:

```bash
ssh elf@192.168.1.113
bash /mnt/sdcard/reconstruct/fix_audio.sh
source ~/ros2/install/setup.bash
cd ~/cloud-model
python3 main.py
```

## 2026-06-20 危险物误报修正

现场误报:

- 两次 `hazard_candidate` 实际没有拿任何东西。
- `rknn_status.json` 中危险物候选为低置信 `knife`，置信度分别约 `0.06` 和 `0.08`。
- 由于旧 VLM prompt 直接传入完整 RKNN JSON，包括 `knife`、`contact=true`、框位置等信息，VLM 被元数据暗示后确认了危险。

修正:

- `safety_guard/prompts.py` 不再把完整 `rknn_status` 传给 VLM。
- 新 prompt 只传去标签化摘要:
  - `review_reason`
  - 人数/手数/候选物数量/候选关系数量
  - 手和候选物的最大置信度
  - 简化姿态状态
- 去掉危险物类别、`contact` 结论、框坐标、完整 events。
- system prompt 明确要求: 图像证据优先；摘要只是触发复核，不是危险成立证据；如果图片中没有清楚可见真实刀、剪刀、叉子等尖锐物，必须 `danger=false`。

验证:

- 使用误报事件 `20260620T193246_hazard_candidate_42fa69f5` 的 `rknn_status.json` 生成新 VLM 输入，确认文本中不含 `knife`、不含 `contact`、不含框坐标。

## 2026-06-22 运行态状态日志

调整目标:

- 不恢复 KWS/AWAKE 高频刷屏计数。
- 只在关键边界输出日志，方便现场判断当前是否可以测试。
- 保留可配置低频计数，必要时再打开。

ASR 日志约定:

- 进入/退出休眠监听: `[ASR] enter SLEEP/KWS`、`[ASR] exit SLEEP/KWS`
- 进入/退出唤醒识别: `[ASR] enter AWAKE/VAD+ASR`、`[ASR] exit AWAKE/VAD+ASR`
- AWAKE 下 VAD speaking 状态变化: `[AWAKE] speaking: false -> true`、`[AWAKE] speaking: true -> false`
- KWS 计数默认关闭；如需调试，设置 `ASR_KWS_PROGRESS_EVERY=50`。
- AWAKE 计数默认关闭；如需调试，设置 `ASR_AWAKE_PROGRESS_EVERY=100`。

启动日志约定:

- `main.py` 使用 `StartupProfiler` 统计主要模块加载耗时。
- 初始化完成后输出 `[startup] event=summary` 和 `[system] event=ready message=初始化完成，可以开始测试。`
- 当前统计模块包括 AEC、TTS、书本数据库匹配、Conversation、固定语录、Dashboard、ASR、麦克风、安全守护和运行时调度器。

## 2026-06-22 唤醒卡顿与 dashboard 污染修正

现场现象:

- 唤醒后固定语录走实时 TTS 生成，网络连接 5s 超时，语音在休眠后才补播，体验卡顿。
- dashboard 首页出现多条 `你好小智`。
- 空闲超时时偶尔连续打印两次 `[休眠] 5秒无对话`。

根因:

- `wake` 固定语录 fallback 曾设为 `None`，随机到未缓存 wake phrase 时会现场连 DashScope 生成 WAV。
- `dashboard_records/dashboard.db` 中存在之前 endpoint 测试写入的测试数据，包含 3 条 `你好小智`。
- ASR 从 KWS 切到 AWAKE 后，极端情况下可能把唤醒词尾音作为正式 ASR 结果；主循环此前没有过滤 exact wake word。
- `asr.sleep()` 是异步状态切换，主循环发出 sleep 请求后没有重置 `idle_since`，因此状态应用前可能重复触发 idle 分支。

修正:

- `wake_reply()` 改为只播放本地 `audio/fillers/wake_*.wav`，不调用 `queue_phrase()`，避免唤醒路径现场联网合成。
- 保留 wake 文案池用于文档/后续预生成，但唤醒实时路径不依赖缓存命中。
- 主循环过滤 exact wake words: `你好小智`、`小智小智` 只作为控制词，不进入 `process_utterance()`，不写 dashboard 对话。
- idle sleep 请求后重置 `idle_since`，避免重复打印休眠日志。
- 清理板端 `dashboard_records/dashboard.db` 中明确的测试污染记录。

验证:

- `scripts.test_wake_phrases` 覆盖 wake fallback 和本地 WAV 播放路径。
- `scripts.test_wake_flow` 覆盖 wake callback 不阻塞、不清队列、exact wake word 过滤、idle sleep 计时重置。

## 2026-06-22 运行日志规范化与启动残留检查

目标:

- 不删除必要诊断日志。
- 主循环处理中进度日志保持默认可见，便于手动运行时确认系统仍在执行。
- TTS 相关日志统一为字段式格式，便于现场定位是网络、合成、固定语录缓存还是播放链路问题。
- `start_system.sh` 在启动前检查 Ctrl-C 或异常退出留下的 `main.py` 残留进程，避免重复启动多个主程序争抢麦克风、TTS、dashboard 端口。

日志规范:

- ASR 识别文本:
  - `[识别] 给我讲一个故事。`
  - 主循环拿到有效 ASR 文本后立即输出，并且早于 `asr.sleep()`、TTS/AEC reset 和后台处理线程，保证手动测试时能马上看到用户说了什么。
- 主循环处理中进度:
  - `[处理中] loop=5100 is_awake=False KWS块=4756 qsize=1`
  - 默认每 100 次主循环输出一行。
  - 禁止 `\r` 单行刷新，因为它会破坏故事/回答的流式终端输出。
- TTS 重试:
  - `[tts.realtime] event=retry attempt=1 max_retries=3 error=...`
- TTS 句子完成:
  - `[tts.synth] event=sentence_done elapsed_ms=930 voice=Cherry text=...`
- TTS 句子错误:
  - `[tts.synth] event=sentence_error error=...`
- 固定语录缓存/生成:
  - `[tts.phrase] event=ready source=cache voice=Cherry phrase_id=... text=...`
- 固定语录错误:
  - `[tts.phrase] event=error phrase_id=... error=...`
- 后加模块统一使用小写模块名前缀和 `event=` 字段:
  - `[startup] event=module name=asr_models status=ok elapsed_ms=...`
  - `[system] event=ready message=初始化完成，可以开始测试。`
  - `[main] event=asr_sleep_done awake=True qsize=0`
  - `[wake] event=detected kw=你好小智 processing=False awake=False`
  - `[dashboard] event=speak source=parent text=...`
  - `[scheduler] event=resource_conflict mode=reading conflicts=...`
  - `[safety] event=candidate_queued component=service id=... type=...`
  - `[aec] event=wav_read_failed path=... error=...`
- 保留历史现场锚点，不改名:
  - `[ASR]`、`[AWAKE]`、`[KWS]`、`[识别]`、`[处理中]`、`[过滤]`
- 禁止继续新增混合前缀:
  - `[_on_wake]`、`[主循环]`、`[处理线程]`、`[Dashboard]`、`[Scheduler]`、`[SafetyGuard]`

启动脚本行为:

- `scripts/start_system.sh --status` 现在会同时报告 pidfile 状态和无 pidfile 的残留 `main.py` 进程。
- `scripts/start_system.sh --stop` 会停止 pidfile 记录进程，并额外清理当前项目根目录下运行的残留 `python main.py`。
- 正常启动前会检查残留进程；如果发现残留，会提示先 `--stop`，避免重复启动。

TTS 健康检查:

```bash
cd ~/cloud-model-safety-mainline
python3 -m scripts.check_tts_health
```

示例输出:

```text
TTS_HEALTH event=config api_key_set=True timeout=30 retries=3
TTS_HEALTH event=tcp status=ok host=dashscope.aliyuncs.com ...
TTS_HEALTH event=synthesize status=ok pcm_bytes=34560 elapsed_ms=930
```

这只能证明网络和 DashScope 实时 TTS 合成链路可用；如果主程序仍没声音，需要继续看播放设备 `DEVICE_SPK`、`aplay`、TTS 队列取消状态和实际主程序日志。
