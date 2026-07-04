"""Fixed phrase playback backed by the global generated phrase cache."""
import os
import random

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fillers")
_speaker = None

_PHRASES = {
    "wake": [
        ("filler.wake.01", "我在。"),
        ("filler.wake.02", "请说。"),
        ("filler.wake.03", "你好。"),
        ("filler.wake.04", "我来了。"),
        ("filler.wake.05", "我听着。"),
    ],
    "think": [
        ("filler.think.01", "我想一下。"),
        ("filler.think.02", "我看看。"),
        ("filler.think.03", "稍等一下。"),
        ("filler.think.04", "让我想想。"),
    ],
    "photo": [
        ("filler.photo.01", "我看一下。"),
        ("filler.photo.02", "我帮你看看。"),
    ],
    "reading_photo": [
        ("filler.reading_photo.look.01", "我看一下。"),
    ],
    "reading_in": [
        ("filler.reading_in.01", "好的，我们开始读书。"),
    ],
    "reading_next_page": [
        ("filler.reading_next_page.01", "好的，继续读下一页。"),
    ],
    "reading_retry": [
        ("filler.reading_retry.01", "好的，我再试一次。"),
    ],
    "reading_out": [
        ("filler.reading_out.01", "好的，先不读书了。"),
    ],
    "reading_continue": [
        ("filler.reading_continue.01", "要继续读下一页吗？"),
    ],
}

_FALLBACKS = {
    "wake": "wake_",
    "think": "think_",
    "photo": "photo_",
    "reading_photo": None,
    "reading_in": "reading_in.wav",
    "reading_next_page": None,
    "reading_retry": None,
    "reading_out": "reading_out.wav",
    "reading_continue": "reading_continue.wav",
}


def set_speaker(speaker):
    global _speaker
    _speaker = speaker


def _fallback_path(kind: str):
    fallback = _FALLBACKS.get(kind)
    if not fallback:
        return None
    if fallback.endswith(".wav"):
        path = os.path.join(_DIR, fallback)
        return path if os.path.exists(path) else None
    if not os.path.isdir(_DIR):
        return None
    files = [os.path.join(_DIR, f) for f in os.listdir(_DIR) if f.startswith(fallback)]
    return random.choice(files) if files else None


def _queue_phrase(kind: str, random_choice: bool = True):
    if not _speaker:
        return
    phrases = _PHRASES.get(kind, [])
    if not phrases:
        fallback = _fallback_path(kind)
        if fallback:
            _speaker.queue_wav(fallback)
        return
    phrase_id, text = random.choice(phrases) if random_choice else phrases[0]
    fallback = _fallback_path(kind)
    if hasattr(_speaker, "queue_phrase"):
        _speaker.queue_phrase(phrase_id, text, voice="Cherry", fallback_wav=fallback)
    elif fallback:
        _speaker.queue_wav(fallback)
    else:
        _speaker.feed(text)
        _speaker.flush()


def wake_reply():
    if not _speaker:
        return
    fallback = _fallback_path("wake")
    if fallback:
        _speaker.queue_wav(fallback)


def think_filler():
    _queue_phrase("think")


def photo_filler():
    """仅在调用 take_photo 时使用"""
    _queue_phrase("photo")


def reading_photo_filler():
    """读书模式首次拍照提示"""
    _queue_phrase("reading_photo", random_choice=False)


def reading_in_filler():
    """读书模式进入播报"""
    _queue_phrase("reading_in", random_choice=False)


def reading_next_page_filler():
    """读书模式继续下一页确认"""
    _queue_phrase("reading_next_page", random_choice=False)


def reading_retry_filler():
    """读书模式失败后重试确认"""
    _queue_phrase("reading_retry", random_choice=False)


def reading_out_filler():
    """读书模式退出播报"""
    _queue_phrase("reading_out", random_choice=False)


def reading_continue_filler():
    """读书模式翻页询问"""
    _queue_phrase("reading_continue", random_choice=False)
