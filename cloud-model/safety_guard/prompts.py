"""Prompts for VLM-based safety risk review."""
from __future__ import annotations

import json


SAFETY_ANALYSIS_SYSTEM_PROMPT = """
你是儿童居家安全复核模型。你的任务是根据一张未标注的实时图像和一段去标签化视觉摘要，判断是否需要现场安全播报。

只输出一个 JSON 对象，不要输出 Markdown，不要解释 JSON 之外的内容。

判定原则：
1. 图像证据优先。视觉摘要只表示程序触发了复核，可能误报，不能当作危险已经成立的证据。
2. 如果图像中明显有人摔倒、躺倒后无明显正常活动，danger=true。
3. 只有当图像中清楚可见真实刀、剪刀、叉子等尖锐物，且人的手正在拿起、接触或非常靠近该物品时，sharp_object 才能 danger=true。
4. 如果图像中看不到清楚的真实尖锐物，或者只是衣物边缘、手部轮廓、背景设备、阴影、反光、检测框误报，danger=false。
5. 不要因为视觉摘要中出现候选数量、置信度或复核类型就确认危险；evidence 必须描述图像里能直接看见的内容。
6. 看不清时不要播报，danger=false，但 summary 中说明疑似原因。
7. tts 字段必须短、直接、适合现场播放给儿童听，不要恐吓，不要提到模型或检测。
8. 摔倒类 tts 不要说“快站起来”，避免儿童受伤后二次风险；应提示“先不要乱动/等待大人帮助”。
9. tts 不要使用“地滑”“小心地滑”等容易被 TTS 读错的短语；如需表达地面湿滑，请写“地面可能很滑”。

JSON schema：
{
  "danger": true,
  "risk_type": "fall|sharp_object|unknown",
  "severity": "low|medium|high|critical",
  "summary": "一句话描述判断",
  "tts": "危险时播报的话；不危险时为空字符串",
  "evidence": ["只写图像中可见证据，不写检测器结论"],
  "recommended_action": "给家长的建议"
}
""".strip()


def build_safety_user_text(candidate_type: str, rknn_status: dict) -> str:
    summary = _safe_visual_summary(candidate_type, rknn_status)
    return (
        "请复核这张未标注的实时图像。\n"
        "视觉程序触发了安全复核，但它可能误报。请不要根据检测器候选直接下结论。\n"
        "如果图片中没有清楚看见真实尖锐物，不能判断为尖锐物危险。\n"
        "去标签化视觉摘要如下(JSON，不含危险物类别、不含接触结论、不含框坐标):\n"
        f"{json.dumps(summary, ensure_ascii=False, sort_keys=True)}\n"
        "请只按指定 JSON schema 输出。"
    )


def _safe_visual_summary(candidate_type: str, status: dict) -> dict:
    counts = status.get("counts") if isinstance(status.get("counts"), dict) else {}
    tracks = status.get("tracks") if isinstance(status.get("tracks"), list) else []
    hands = status.get("hands") if isinstance(status.get("hands"), list) else []
    hazards = status.get("hazards") if isinstance(status.get("hazards"), list) else []
    relations = status.get("relations") if isinstance(status.get("relations"), list) else []

    return {
        "review_reason": _review_reason(candidate_type),
        "image_is_unannotated": True,
        "metadata_is_not_evidence": True,
        "people_count": int(counts.get("persons", 0) or 0),
        "hand_count": int(counts.get("hands", 0) or 0),
        "object_candidate_count": int(counts.get("hazards", 0) or 0),
        "nearby_pair_candidate_count": int(counts.get("relations", 0) or 0),
        "object_candidate_label_hidden": True,
        "object_candidate_conf_max": _max_conf(hazards),
        "hand_candidate_conf_max": _max_conf(hands),
        "pose_track_states": _safe_track_states(tracks),
    }


def _review_reason(candidate_type: str) -> str:
    if candidate_type == "fall_candidate":
        return "posture_review"
    if candidate_type == "hazard_candidate":
        return "hand_near_object_review"
    if candidate_type == "combined_candidate":
        return "posture_and_hand_near_object_review"
    return "safety_review"


def _max_conf(items: list) -> float:
    vals = []
    for item in items:
        try:
            vals.append(float(item.get("conf", 0.0)))
        except Exception:
            pass
    return round(max(vals), 3) if vals else 0.0


def _safe_track_states(tracks: list) -> list:
    out = []
    for track in tracks[:3]:
        state = str(track.get("state", "unknown"))
        out.append({
            "state": state if state in {"OK", "LYING", "FALL", "STILL"} else "unknown",
            "torso": round(float(track.get("torso", 0.0) or 0.0), 1),
            "aspect": round(float(track.get("aspect", 0.0) or 0.0), 2),
        })
    return out
