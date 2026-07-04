"""Pure helpers for reading-mode response handling."""
from typing import Optional


NEXT_PAGE_QUESTION_MARKER = "继续读下一页吗"

RETRY_MARKERS = (
    "需要再试一次",
    "再试一次吗",
    "再试一次",
    "再拍一次",
    "重新拍",
    "重拍",
    "再帮您拍",
    "再帮你拍",
    "需要小智再帮",
    "需要再帮",
    "拍照失败",
    "没拍到",
    "拍不了",
    "照片模糊",
    "文字模糊",
    "光线太暗",
    "放正一点",
    "离镜头近一点",
    "看不清",
    "无法看清",
    "无法识别",
    "没有文字",
    "未识别到文字",
    "调整角度",
    "调整距离",
)


def _normalized_text(response_text: Optional[str]) -> str:
    return "".join((response_text or "").split())


def _response_has_retry_marker(normalized: str) -> bool:
    return any(marker in normalized for marker in RETRY_MARKERS)


def classify_reading_turn(response_text: Optional[str], tool_result: Optional[dict] = None) -> dict:
    """Classify whether one reading turn can advance to the next page.

    A page can be considered successful only when the reading image capture
    succeeded and the model response is not a retry/failure response.
    """
    normalized = _normalized_text(response_text)
    capture_ok = bool((tool_result or {}).get("capture_ok"))
    has_text = bool(normalized)
    retry = _response_has_retry_marker(normalized)
    asked_next_page = NEXT_PAGE_QUESTION_MARKER in normalized
    model_success = has_text and not retry
    successful = capture_ok and model_success
    prompt_next_page = successful and not asked_next_page
    return {
        "capture_ok": capture_ok,
        "has_text": has_text,
        "retry": retry,
        "asked_next_page": asked_next_page,
        "model_success": model_success,
        "successful": successful,
        "prompt_next_page": prompt_next_page,
    }


def should_prompt_next_page(response_text: Optional[str]) -> bool:
    """Return whether a successful reading needs the fixed next-page prompt."""
    return classify_reading_turn(response_text, {"capture_ok": True})["prompt_next_page"]


READING_CONTEXT_MARKERS = ("OCR朗读", "读书")

READING_STREAM_SKIP_MARKERS = (
    "我是小智",
    "正在读书模式",
    "正在为你朗读",
    "为你朗读",
    "请稍等",
    "注：",
    "注:",
    "以上为照片",
    "可见文字",
    "按原顺序逐字朗读",
    "未作任何",
    "增减或解释",
)

READING_SENTENCE_ENDS = "。！？"


def is_reading_context(system_prompt: str) -> bool:
    """Return whether a conversation is currently in reading/OCR mode."""
    return any(marker in (system_prompt or "") for marker in READING_CONTEXT_MARKERS)


class ReadingStreamFilter:
    """Filter reading-mode OCR output while keeping sentence-level streaming."""

    def __init__(self):
        self._buffer = ""

    def feed(self, text: str) -> str:
        self._buffer += text or ""
        parts = []
        while True:
            boundary = self._next_boundary()
            if boundary is None:
                break
            end, include_newline = boundary
            segment, self._buffer = self._buffer[:end], self._buffer[end:]
            emitted = self._filter_segment(segment, include_newline=include_newline)
            if emitted:
                parts.append(emitted)
        return "".join(parts)

    def flush(self) -> str:
        segment = self._buffer
        self._buffer = ""
        return self._filter_segment(segment, include_newline=False)

    def _next_boundary(self):
        candidates = []
        newline_pos = self._buffer.find("\n")
        if newline_pos != -1:
            candidates.append((newline_pos + 1, True))
        sentence_positions = [
            self._buffer.find(ch)
            for ch in READING_SENTENCE_ENDS
            if self._buffer.find(ch) != -1
        ]
        if sentence_positions:
            candidates.append((min(sentence_positions) + 1, False))
        if not candidates:
            return None
        return min(candidates, key=lambda item: item[0])

    def _filter_segment(self, segment: str, include_newline: bool) -> str:
        segment = segment.rstrip("\r\n")
        stripped = segment.strip()
        if not stripped:
            return ""
        if self._should_drop(stripped):
            return ""
        return segment + ("\n" if include_newline else "")

    def _should_drop(self, stripped: str) -> bool:
        if stripped.startswith(("（注", "(注")):
            return True
        return any(marker in stripped for marker in READING_STREAM_SKIP_MARKERS)
