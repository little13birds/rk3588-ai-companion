"""Global generated WAV cache for fixed phrases."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Tuple

from config import TTS_REALTIME_SAMPLE_RATE


CACHE_VERSION = 1
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent / "audio" / "generated_phrases"
MANIFEST_NAME = "manifest.json"

_LOCK = threading.Lock()


@dataclass(frozen=True)
class PhraseCacheKey:
    phrase_id: str
    text: str
    voice: str
    model: str
    instructions: str
    sample_rate: int = TTS_REALTIME_SAMPLE_RATE

    @property
    def signature(self) -> str:
        payload = {
            "version": CACHE_VERSION,
            "phrase_id": self.phrase_id,
            "text": self.text,
            "voice": self.voice,
            "model": self.model,
            "instructions": self.instructions,
            "sample_rate": self.sample_rate,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cache_dir() -> Path:
    return Path(os.environ.get("TTS_PHRASE_CACHE_DIR", str(DEFAULT_CACHE_DIR))).expanduser()


def _safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return value[:72].strip("._") or "phrase"


def _wav_path(base_dir: Path, key: PhraseCacheKey) -> Path:
    return base_dir / f"{_safe_name(key.phrase_id)}__{key.signature[:16]}.wav"


def _manifest_path(base_dir: Path) -> Path:
    return base_dir / MANIFEST_NAME


def _load_manifest(base_dir: Path) -> dict:
    path = _manifest_path(base_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_manifest(base_dir: Path, manifest: dict) -> None:
    path = _manifest_path(base_dir)
    tmp = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(base_dir), delete=False)
    try:
        with tmp:
            json.dump(manifest, tmp, ensure_ascii=False, indent=2, sort_keys=True)
            tmp.write("\n")
        os.replace(tmp.name, path)
    finally:
        try:
            if os.path.exists(tmp.name):
                os.remove(tmp.name)
        except Exception:
            pass


def _write_wav_atomic(path: Path, pcm: bytes, sample_rate: int) -> None:
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", dir=str(path.parent), delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        with wave.open(tmp_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm)
        os.replace(tmp_path, path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def cached_wav_path(key: PhraseCacheKey) -> Path | None:
    base = cache_dir()
    path = _wav_path(base, key)
    if path.exists() and path.stat().st_size > 44:
        return path
    return None


def get_or_create_wav(
    key: PhraseCacheKey,
    synthesize_pcm: Callable[[], bytes],
) -> Tuple[Path, bool]:
    """Return cached/generated WAV path and whether it was already cached."""
    base = cache_dir()
    base.mkdir(parents=True, exist_ok=True)
    path = _wav_path(base, key)

    with _LOCK:
        if path.exists() and path.stat().st_size > 44:
            return path, True

        pcm = synthesize_pcm()
        if not pcm:
            raise RuntimeError(f"empty PCM for phrase {key.phrase_id}")
        _write_wav_atomic(path, pcm, key.sample_rate)

        manifest = _load_manifest(base)
        manifest[key.signature] = {
            "phrase_id": key.phrase_id,
            "text": key.text,
            "voice": key.voice,
            "model": key.model,
            "instructions": key.instructions,
            "sample_rate": key.sample_rate,
            "path": path.name,
            "created_at": int(time.time()),
        }
        _save_manifest(base, manifest)
        return path, False
