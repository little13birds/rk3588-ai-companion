"""Mode policies for cloud-model runtime scheduling."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from .resources import Resource


PRIORITY_NORMAL = 10
PRIORITY_READING = 50
PRIORITY_SAFETY_ALERT = 100


@dataclass(frozen=True)
class ModePolicy:
    mode: str
    owner: str
    priority: int
    resources: Tuple[Resource, ...]
    preempt: bool = False
    reason: str = ""


NORMAL_POLICY = ModePolicy(
    mode="normal",
    owner="mode.normal",
    priority=PRIORITY_NORMAL,
    resources=(
        Resource.MIC_ASR_KWS,
        Resource.ROS_RGB_CAMERA,
        Resource.NPU_CORE_0,
        Resource.NPU_CORE_1,
    ),
    preempt=False,
    reason="normal assistant + safety guard",
)


READING_POLICY = ModePolicy(
    mode="reading",
    owner="mode.reading",
    priority=PRIORITY_READING,
    resources=(
        Resource.ROARM_SERIAL,
        Resource.ARM_AGENT_HTTP,
        Resource.ROS_RGB_CAMERA,
        Resource.NPU_CORE_2,
        Resource.SPEAKER_TTS,
    ),
    preempt=True,
    reason="reading mode arm/book detection",
)


SAFETY_ALERT_POLICY = ModePolicy(
    mode="safety_alert",
    owner="mode.safety_alert",
    priority=PRIORITY_SAFETY_ALERT,
    resources=(Resource.SPEAKER_TTS,),
    preempt=True,
    reason="high priority safety announcement",
)
