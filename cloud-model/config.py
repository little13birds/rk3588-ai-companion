"""项目统一配置。

Public release note:
API keys are read from environment variables. Do not commit real keys.
"""

import os

# 阿里云百炼 — OpenAI 兼容接口
LLM_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
LLM_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
LLM_MODEL = "qwen3-vl-flash"

# TTS — DashScope 原生接口
TTS_API_KEY = os.getenv("DASHSCOPE_TTS_API_KEY", LLM_API_KEY)
TTS_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"

# 音频
SAMPLE_RATE = 16000
CHANNELS = 1
DEVICE_MIC = "plughw:Device,0"
DEVICE_SPK = "plughw:Device,0"

# 摄像头
CAMERA_INDEX = 21

# 显示屏
DISPLAY_WIDTH = 1024
DISPLAY_HEIGHT = 600

# === 实时 TTS (WebSocket) ===
TTS_REALTIME_SAMPLE_RATE = 24000       # 实时TTS输出采样率
TTS_REALTIME_SILENCE_MS = 200          # 句间静音时长
TTS_REALTIME_TIMEOUT = 30              # commit 超时(秒)
TTS_REALTIME_MAX_RETRIES = 3           # WebSocket 重连次数

# === 机械臂 arm_agent（ROS2 侧 HTTP 接口）===
ARM_AGENT_URL = "http://127.0.0.1:8642"   # arm_agent 内嵌 HTTP 服务地址
