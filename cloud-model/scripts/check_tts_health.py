"""Quick board-side realtime TTS health check.

Run from repo root:
    python3 -m scripts.check_tts_health
"""
from __future__ import annotations

import socket
import time

from config import TTS_API_KEY, TTS_REALTIME_MAX_RETRIES, TTS_REALTIME_TIMEOUT
from tts.realtime_tts import RealtimeTTSSession


def _check_tcp(host: str = "dashscope.aliyuncs.com", port: int = 443) -> tuple[bool, str]:
    started = time.time()
    try:
        ip = socket.gethostbyname(host)
        sock = socket.create_connection((host, port), timeout=5)
        sock.close()
        return True, "host={} ip={} port={} elapsed_ms={}".format(
            host, ip, port, int((time.time() - started) * 1000)
        )
    except Exception as exc:
        return False, "host={} port={} error={}:{} elapsed_ms={}".format(
            host, port, type(exc).__name__, exc, int((time.time() - started) * 1000)
        )


def main() -> int:
    print(
        "TTS_HEALTH event=config api_key_set={} timeout={} retries={}".format(
            bool(TTS_API_KEY),
            TTS_REALTIME_TIMEOUT,
            TTS_REALTIME_MAX_RETRIES,
        ),
        flush=True,
    )
    ok, detail = _check_tcp()
    print("TTS_HEALTH event=tcp status={} {}".format("ok" if ok else "fail", detail), flush=True)
    if not ok:
        return 2

    started = time.time()
    session = RealtimeTTSSession(voice="Cherry")
    try:
        pcm = session.synthesize("测试。")
        elapsed_ms = int((time.time() - started) * 1000)
        print(
            "TTS_HEALTH event=synthesize status=ok pcm_bytes={} elapsed_ms={}".format(
                len(pcm), elapsed_ms
            ),
            flush=True,
        )
        return 0 if pcm else 3
    except Exception as exc:
        elapsed_ms = int((time.time() - started) * 1000)
        print(
            "TTS_HEALTH event=synthesize status=fail error={}:{} elapsed_ms={}".format(
                type(exc).__name__, str(exc).replace("\n", " ")[:260], elapsed_ms
            ),
            flush=True,
        )
        return 1
    finally:
        try:
            session.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
