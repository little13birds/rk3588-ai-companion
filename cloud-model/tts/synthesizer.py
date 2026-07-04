"""TTS 共享常量 — VOICES, 切句规则, 队列工具"""
import re, queue

VOICES = {
    "Cherry": "Cherry", "Serena": "Serena", "Ethan": "Ethan",
    "Chelsie": "Chelsie", "Momo": "Momo", "Vivian": "Vivian",
    "Moon": "Moon", "Maia": "Maia", "Kai": "Kai", "Nofish": "Nofish",
    "Adam": "Adam", "Bella": "Bella", "EldricSage": "Eldric Sage", "Mia": "Mia",
    "Mochi": "Mochi", "Bellona": "Bellona", "Vincent": "Vincent",
    "Bunny": "Bunny", "Neil": "Neil", "Elias": "Elias",
    "Arthur": "Arthur", "Nini": "Nini", "Ebona": "Ebona",
    "Seren": "Seren", "Pip": "Pip", "Stella": "Stella",
}

SENTENCE_ENDS = set("。！？\n")
TTS_SOFT_ENDS = set("，、；：,;:")
VOICE_TAG_RE = re.compile(r"\[([A-Za-z]+)\]\s*")

_SOFT_SPLIT_MIN_TEXT = 24
_SOFT_SPLIT_MIN_PREFIX = 4
_SOFT_SPLIT_MIN_REMAINING = 6


def _ensure_terminal_punctuation(text: str) -> str:
    sentence = (text or "").strip()
    if not sentence:
        return ""
    if sentence[-1] in SENTENCE_ENDS:
        return sentence
    if sentence[-1] in TTS_SOFT_ENDS:
        sentence = sentence.rstrip("".join(TTS_SOFT_ENDS)).strip()
    return sentence + "。"


def split_tts_text(text: str, ensure_terminal_punctuation: bool = False) -> list[str]:
    """Normalize one TTS sentence and optionally split long text at natural pauses."""
    sentence = (text or "").strip()
    if ensure_terminal_punctuation:
        sentence = _ensure_terminal_punctuation(sentence)
    if not sentence:
        return []
    if len(sentence) < _SOFT_SPLIT_MIN_TEXT:
        return [sentence]

    parts = []
    start = 0
    for idx, ch in enumerate(sentence):
        if ch not in TTS_SOFT_ENDS:
            continue
        prefix_len = idx - start + 1
        remaining_len = len(sentence) - idx - 1
        if prefix_len < _SOFT_SPLIT_MIN_PREFIX or remaining_len < _SOFT_SPLIT_MIN_REMAINING:
            continue
        part = _ensure_terminal_punctuation(sentence[start:idx + 1])
        if part:
            parts.append(part)
        start = idx + 1

    tail = _ensure_terminal_punctuation(sentence[start:])
    if tail:
        parts.append(tail)
    return parts or [sentence]


def _clear_queue(q: queue.Queue):
    """清空队列，对每个未完成任务调用 task_done"""
    try:
        while True:
            q.get_nowait()
            q.task_done()
    except queue.Empty:
        pass
