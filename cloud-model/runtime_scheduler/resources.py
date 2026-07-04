"""Resource definitions used by the runtime scheduler."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional


class Resource(str, Enum):
    USB_V4L2_CAMERA = "usb_v4l2_camera"
    ROS_RGB_CAMERA = "ros_rgb_camera"
    ROS_DEPTH_CAMERA = "ros_depth_camera"
    NPU_CORE_0 = "npu_core_0"
    NPU_CORE_1 = "npu_core_1"
    NPU_CORE_2 = "npu_core_2"
    NPU_SAFETY = "npu_safety"
    NPU_BOOK = "npu_book"
    NPU_PERSON_FACE = "npu_person_face"
    CPU_VISION = "cpu_vision"
    SPEAKER_TTS = "speaker_tts"
    MIC_ASR_KWS = "mic_asr_kws"
    ROARM_SERIAL = "roarm_serial"
    ARM_AGENT_HTTP = "arm_agent_http"


@dataclass(frozen=True)
class ResourceInfo:
    shared: bool = False
    max_shared: int = 1
    conflict_group: Optional[str] = None
    description: str = ""


RESOURCE_INFO: Dict[Resource, ResourceInfo] = {
    Resource.USB_V4L2_CAMERA: ResourceInfo(description="arm reading camera direct /dev/video* reader"),
    Resource.ROS_RGB_CAMERA: ResourceInfo(shared=True, max_shared=4, description="platform depth/RGB camera ROS RGB topic"),
    Resource.ROS_DEPTH_CAMERA: ResourceInfo(shared=True, max_shared=4, description="platform depth/RGB camera ROS depth topic"),
    Resource.NPU_CORE_0: ResourceInfo(description="RK3588 NPU physical core 0"),
    Resource.NPU_CORE_1: ResourceInfo(description="RK3588 NPU physical core 1"),
    Resource.NPU_CORE_2: ResourceInfo(description="RK3588 NPU physical core 2"),
    Resource.NPU_SAFETY: ResourceInfo(conflict_group="npu", description="safety pose/hand/hazard RKNN"),
    Resource.NPU_BOOK: ResourceInfo(conflict_group="npu", description="book detection RKNN"),
    Resource.NPU_PERSON_FACE: ResourceInfo(conflict_group="npu", description="person/face RKNN"),
    Resource.CPU_VISION: ResourceInfo(shared=True, max_shared=3, description="CPU-heavy vision work"),
    Resource.SPEAKER_TTS: ResourceInfo(description="RealtimeSpeaker playback"),
    Resource.MIC_ASR_KWS: ResourceInfo(description="microphone ASR/KWS path"),
    Resource.ROARM_SERIAL: ResourceInfo(description="/dev/roarm via roarm_driver"),
    Resource.ARM_AGENT_HTTP: ResourceInfo(description="arm_agent HTTP service"),
}


def resource_info(resource: Resource) -> ResourceInfo:
    return RESOURCE_INFO[resource]
