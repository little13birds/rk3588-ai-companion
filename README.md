# RK3588 AI Companion Robot

本仓库是一个基于 RK3588 的边缘 AI 儿童陪伴机器人项目公开快照。系统面向儿童陪伴互动、伴读早教和家庭安全监护场景，集成语音 Agent、RKNN 端侧视觉推理、ROS2 机器人控制、读书机械臂、平台 RGB-D 相机、环境传感、HDMI 表情显示和家长端 Dashboard。

## 项目能力

- 语音唤醒、ASR/VAD、TTS 播放、打断响应和大模型工具调用。
- 纸质书伴读模式，包括读书机械臂对齐、书页拍摄、透视矫正和本地书本数据库匹配。
- 儿童安全监护，包括摔倒、危险物接近、睡眠在场提醒、事件记录和家长端查看。
- 人物身份观察、指定人物寻找和人物跟随，并通过 ROS2 执行链路接入底盘控制。
- 家长端 Dashboard，用于实时画面、历史记录、安全状态、人物/孩子设置和机器人控制。

## 仓库结构

```text
cloud-model/        主语音 Agent、Dashboard、资源调度、安全守护整合、读书模式、ASR/TTS 和工具调用。
ros2/               ROS2 工作区，包含平台相机、读书机械臂、底盘、避障、人物寻找/跟随等机器人服务。
docs/               公开发布说明、依赖许可审计、省略资产清单和资产恢复说明。
```

## 快照来源

本仓库由两个本地主线快照组合而成：

- `cloud-model/`：来自 `cloud-model-safety-mainline`，源提交 `7d13dc2`。
- `ros2/`：来自 `ros2`，源提交 `2b0bdef`。

两个源仓库原本的 `.git/` 目录没有嵌入本仓库。本仓库用于项目公开展示和交付，不用于完整保留两个源仓库的历史记录。

## 公开发布说明

本公开快照已经移除真实云端 API Key。运行时请通过环境变量提供密钥：

```bash
export DASHSCOPE_API_KEY="your-key"
export DASHSCOPE_TTS_API_KEY="your-tts-key"  # 可选；未设置时默认使用 DASHSCOPE_API_KEY
```

大型第三方资产、模型文件和部分二进制文件没有放入本次公开快照。具体清单见：

- `docs/OMITTED_LARGE_ASSETS.txt`
- `docs/ASSET_RESTORE_GUIDE.md`

## 许可状态

本仓库根目录暂未声明统一开源许可证。原因是 ROS2 工作区中包含多个第三方包、厂商 SDK、模型文件和许可证声明不完整的组件。添加根目录 `LICENSE` 前，请先阅读：

- `docs/DEPENDENCY_LICENSE_AUDIT.md`

## 后续计划

- 评估 PCB 和 3D 建模资料的公开方式。
- 根据许可审计结果决定根目录许可证。
- 根据需要通过 Git LFS、GitHub Release 或外部下载方式补充可公开分发的大型资产。
