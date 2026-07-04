# Reading Photo Latency Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the pre-arm `take_photo` response speed while keeping the arm-side 1280x720 book detector unchanged.

**Architecture:** `arm_agent` continues capturing and detecting books at 1280x720. `cloud-model/vision/camera.py` converts only the JPEG sent to the cloud VLM to a maximum width of 640 pixels while preserving aspect ratio. After a tool result is appended, `Conversation._call_api()` skips the second hidden non-streaming image analysis and goes directly to the existing streaming answer.

**Tech Stack:** Python 3.10, OpenCV, NumPy, OpenAI-compatible DashScope client, ROS2 Humble

---

## File Map

- Modify `vision/camera.py`: prepare a VLM-sized JPEG without changing the arm detector frame.
- Create `vision/test_camera.py`: verify 1280x720 input becomes 640x360 and smaller inputs are not enlarged.
- Modify `llm/chat.py`: after executing tool calls, leave the non-streaming tool-selection loop and start the final streaming response.
- Create `llm/test_tool_streaming.py`: prove a photo tool round uses API modes `[False, True]`, not `[False, False, True]`.
- No changes to `~/ros2/src/face_track/face_track/arm_agent.py`, servo parameters, or book detector model.

### Task 1: Add VLM Image Preparation Tests

**Files:**
- Create: `vision/test_camera.py`
- Test: `vision/test_camera.py`

- [ ] **Step 1: Write the failing resize tests**

```python
"""vision.camera image preparation tests. Run: python3 -m vision.test_camera"""
import base64

import cv2
import numpy as np

from vision import camera


def _jpeg(width, height):
    frame = np.full((height, width, 3), 220, dtype=np.uint8)
    cv2.putText(
        frame, "BOOK TEXT", (20, height // 2),
        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2,
    )
    ok, encoded = cv2.imencode(
        ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95]
    )
    assert ok
    return encoded.tobytes()


def _shape(jpg):
    frame = cv2.imdecode(
        np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR
    )
    assert frame is not None
    return frame.shape[:2]


def test_large_frame_is_resized_for_vlm():
    source = _jpeg(1280, 720)
    result = camera.prepare_vlm_jpeg(source)
    assert _shape(result) == (360, 640)
    assert len(result) < len(source)
    print("test_large_frame_is_resized_for_vlm PASS")


def test_small_frame_is_not_upscaled():
    source = _jpeg(320, 240)
    result = camera.prepare_vlm_jpeg(source)
    assert _shape(result) == (240, 320)
    print("test_small_frame_is_not_upscaled PASS")


def test_capture_encodes_prepared_frame(monkeypatch=None):
    source = _jpeg(1280, 720)
    original = camera.agent_client.get_frame
    camera.agent_client.get_frame = lambda wait_ready=False: source
    try:
        result = base64.b64decode(camera.capture(wait_ready=True))
    finally:
        camera.agent_client.get_frame = original
    assert _shape(result) == (360, 640)
    print("test_capture_encodes_prepared_frame PASS")


if __name__ == "__main__":
    test_large_frame_is_resized_for_vlm()
    test_small_frame_is_not_upscaled()
    test_capture_encodes_prepared_frame()
    print("ALL PASS")
```

- [ ] **Step 2: Run the tests and verify RED**

Run on the board:

```bash
cd ~/cloud-model
python3 -m vision.test_camera
```

Expected: FAIL with `AttributeError: module 'vision.camera' has no attribute 'prepare_vlm_jpeg'`.

- [ ] **Step 3: Implement VLM-only image resizing**

Modify `vision/camera.py`:

```python
"""摄像头操作 — 拍照。帧由 ROS2 侧 arm_agent 提供（cloud-model 不直接打开摄像头）。"""
import base64

import cv2
import numpy as np

from arm import agent_client


VLM_IMAGE_MAX_WIDTH = 640
VLM_JPEG_QUALITY = 95


def prepare_vlm_jpeg(jpg: bytes) -> bytes:
    """缩小送给云端 VLM 的图片；机械臂检测仍使用 arm_agent 原始帧。"""
    frame = cv2.imdecode(
        np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR
    )
    if frame is None:
        return b""

    height, width = frame.shape[:2]
    if width > VLM_IMAGE_MAX_WIDTH:
        target_height = max(
            1, round(height * VLM_IMAGE_MAX_WIDTH / width)
        )
        frame = cv2.resize(
            frame,
            (VLM_IMAGE_MAX_WIDTH, target_height),
            interpolation=cv2.INTER_AREA,
        )

    ok, encoded = cv2.imencode(
        ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, VLM_JPEG_QUALITY]
    )
    return encoded.tobytes() if ok else b""


def capture(wait_ready: bool = False) -> str:
    """拍照并返回适合 VLM 的 base64 JPEG；失败返回空字符串。"""
    jpg = agent_client.get_frame(wait_ready=wait_ready)
    if not jpg:
        return ""
    prepared = prepare_vlm_jpeg(jpg)
    if not prepared:
        return ""
    return base64.b64encode(prepared).decode()
```

- [ ] **Step 4: Run the resize tests and verify GREEN**

```bash
cd ~/cloud-model
python3 -m vision.test_camera
```

Expected:

```text
test_large_frame_is_resized_for_vlm PASS
test_small_frame_is_not_upscaled PASS
test_capture_encodes_prepared_frame PASS
ALL PASS
```

- [ ] **Step 5: Verify full-resolution arm frames remain unchanged**

With the arm stack running:

```bash
curl -s http://127.0.0.1:8642/frame.jpg -o /tmp/arm-full.jpg
python3 - <<'PY'
import cv2
from vision.camera import capture
import base64

full = cv2.imread("/tmp/arm-full.jpg")
vlm = cv2.imdecode(
    __import__("numpy").frombuffer(
        base64.b64decode(capture()), dtype="uint8"
    ),
    cv2.IMREAD_COLOR,
)
print("arm:", full.shape[:2], "vlm:", vlm.shape[:2])
assert full.shape[:2] == (720, 1280)
assert vlm.shape[:2] == (360, 640)
PY
```

Expected: `arm: (720, 1280) vlm: (360, 640)`.

- [ ] **Step 6: Commit the image preparation change on the board**

```bash
cd ~/cloud-model
git add vision/camera.py vision/test_camera.py
git commit -m "perf: 缩小送入VLM的读书图片"
```

Only commit after the user explicitly requests a commit.

### Task 2: Stream Immediately After Tool Execution

**Files:**
- Create: `llm/test_tool_streaming.py`
- Modify: `llm/chat.py:132-181`
- Test: `llm/test_tool_streaming.py`

- [ ] **Step 1: Write the failing API-call sequence test**

```python
"""Tool execution must transition directly to the streaming answer."""
from types import SimpleNamespace

from llm import chat


class FakeCompletions:
    def __init__(self):
        self.stream_modes = []

    def create(self, **kwargs):
        self.stream_modes.append(kwargs["stream"])
        if len(self.stream_modes) == 1:
            tool_call = SimpleNamespace(
                id="call-1",
                function=SimpleNamespace(
                    name="take_photo", arguments="{}"
                ),
            )
            message = SimpleNamespace(
                content=None, tool_calls=[tool_call]
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message)],
                usage=None,
            )

        assert kwargs["stream"] is True
        delta = SimpleNamespace(content="朗读完成")
        return [
            SimpleNamespace(
                choices=[SimpleNamespace(delta=delta)],
                usage=None,
            )
        ]


def test_tool_result_goes_directly_to_streaming_answer():
    fake_completions = FakeCompletions()
    original_client = chat.client
    chat.client = SimpleNamespace(
        chat=SimpleNamespace(completions=fake_completions)
    )
    conv = chat.Conversation(
        system_prompt="你是OCR朗读器。",
        max_tokens=40,
        tools=[{"type": "function"}],
    )
    conv.messages.append({"role": "user", "content": "读书"})
    conv._execute_tool = lambda name, args: [
        {"type": "text", "text": "模拟图片内容"}
    ]
    try:
        result = conv._call_api()
    finally:
        chat.client = original_client

    assert fake_completions.stream_modes == [False, True]
    assert result["text"] == "朗读完成"
    print("test_tool_result_goes_directly_to_streaming_answer PASS")


if __name__ == "__main__":
    test_tool_result_goes_directly_to_streaming_answer()
    print("ALL PASS")
```

- [ ] **Step 2: Run the test and verify RED**

```bash
cd ~/cloud-model
python3 -m llm.test_tool_streaming
```

Expected: FAIL because the old code makes a second `stream=False` call after `take_photo`.

- [ ] **Step 3: Exit the tool-selection loop after executing tools**

Modify the `if msg.tool_calls:` branch in `llm/chat.py`:

```python
                if msg.tool_calls:
                    self.messages.append({
                        "role": "assistant",
                        "content": msg.content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in msg.tool_calls
                        ],
                    })
                    tool_started = time.time()
                    for tc in msg.tool_calls:
                        result = self._execute_tool(
                            tc.function.name,
                            tc.function.arguments,
                        )
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        })
                    elapsed = time.time() - tool_started
                    print(
                        "[tool] {} 已执行 ({:.1f}s)，开始流式回答".format(
                            ",".join(
                                tc.function.name
                                for tc in msg.tool_calls
                            ),
                            elapsed,
                        ),
                        flush=True,
                    )
                    break
```

Rationale: all tool calls returned in the same assistant message are still executed. The following existing `stream=True` request consumes their results exactly once. Do not add `tools=self.tools` to that final streaming request, otherwise the model may request the same photo again.

- [ ] **Step 4: Run the test and verify GREEN**

```bash
cd ~/cloud-model
python3 -m llm.test_tool_streaming
```

Expected:

```text
[tool] take_photo 已执行 (0.0s)，开始流式回答
朗读完成
test_tool_result_goes_directly_to_streaming_answer PASS
ALL PASS
```

- [ ] **Step 5: Run existing conversation syntax and import checks**

```bash
cd ~/cloud-model
python3 -m py_compile llm/chat.py llm/test_tool_streaming.py
python3 - <<'PY'
from llm.chat import Conversation
from vision.camera import prepare_vlm_jpeg
print("imports PASS")
PY
```

Expected: `imports PASS`.

- [ ] **Step 6: Commit the tool-flow change on the board**

```bash
cd ~/cloud-model
git add llm/chat.py llm/test_tool_streaming.py
git commit -m "perf: 工具执行后直接流式生成回答"
```

Only commit after the user explicitly requests a commit.

### Task 3: Board Integration and Latency Verification

**Files:**
- Verify: `vision/camera.py`
- Verify: `llm/chat.py`
- Verify: `main.py`

- [ ] **Step 1: Run all focused tests**

```bash
cd ~/cloud-model
python3 -m vision.test_camera
python3 -m llm.test_tool_streaming
python3 -m arm.test_agent_client
python3 -m py_compile vision/camera.py llm/chat.py main.py
git diff --check
```

Expected: every test prints `ALL PASS`; `py_compile` and `git diff --check` exit 0.

- [ ] **Step 2: Verify the arm stack is healthy before the speech test**

```bash
~/ros2/start_reading_arm.sh
curl -s http://127.0.0.1:8642/book/status
```

Expected: JSON is returned and `tracking` is `false` before entering reading mode.

- [ ] **Step 3: Run the end-to-end reading test**

```bash
cd ~/cloud-model
python3 main.py
```

Say:

```text
你好小智
进入读书模式
```

Expected sequence:

```text
[tool] take_photo 已执行 (...s)，开始流式回答
```

Then the first OCR text should begin streaming without another hidden full-image request. Under the network conditions measured on June 13, 2026, a 640-wide representative frame had about 2.8 seconds TTFT versus about 6.7 seconds at 1280x720. Treat these as comparison data, not a hard network SLA.

- [ ] **Step 4: Verify no behavior regression**

During the same run:

1. With no book visible, `/frame.jpg?wait_ready=1&timeout=25` must still return HTTP 409.
2. With a found, aligned, settled book, `/book/status` must show `ready=true`.
3. The arm must still search and align using its 1280x720 internal frame.
4. A normal-mode `take_photo` must still return a visual answer.
5. Reading-mode exit and interruption must still call `stop_reading()`.

- [ ] **Step 5: Stop processes after verification**

```bash
~/ros2/stop_reading_arm.sh
```

If `main.py` is still running, stop it separately with `Ctrl+C`.

### Task 4: Sync Board Source Back to Windows

**Files:**
- Sync: `~/cloud-model/vision/camera.py` to `D:\嵌赛项目\云端模型\vision\camera.py`
- Sync: `~/cloud-model/vision/test_camera.py` to `D:\嵌赛项目\云端模型\vision\test_camera.py`
- Sync: `~/cloud-model/llm/chat.py` to `D:\嵌赛项目\云端模型\llm\chat.py`
- Sync: `~/cloud-model/llm/test_tool_streaming.py` to `D:\嵌赛项目\云端模型\llm\test_tool_streaming.py`

- [ ] **Step 1: Copy verified board files to Windows**

Run from Windows:

```powershell
scp elf@192.168.1.113:/home/elf/cloud-model/vision/camera.py `
    elf@192.168.1.113:/home/elf/cloud-model/vision/test_camera.py `
    "D:\嵌赛项目\云端模型\vision\"
scp elf@192.168.1.113:/home/elf/cloud-model/llm/chat.py `
    elf@192.168.1.113:/home/elf/cloud-model/llm/test_tool_streaming.py `
    "D:\嵌赛项目\云端模型\llm\"
```

- [ ] **Step 2: Verify board and Windows hashes match**

Calculate SHA-256 on both sides for the four files and require exact matches before reporting completion.

## Completion Criteria

- Arm detection and alignment continue at 1280x720.
- Images sent to the VLM are at most 640 pixels wide and preserve aspect ratio.
- A photo tool round makes one non-streaming tool-selection request followed by one streaming answer request.
- The API call sequence test records exactly `[False, True]`.
- Reading mode, normal photo mode, interruption, and failed-search HTTP 409 behavior still work.
- Board and Windows copies have matching SHA-256 hashes.
