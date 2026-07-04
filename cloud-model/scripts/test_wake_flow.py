"""Regression tests for wake-up audio handoff.

Run from repo root:
    python3 -m scripts.test_wake_flow
"""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"


def _wake_callback() -> ast.FunctionDef:
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_on_wake":
            return node
    raise AssertionError("main.py does not define _on_wake")


def _main_source() -> str:
    return MAIN.read_text(encoding="utf-8")


def _call_name(node: ast.Call) -> str:
    func = node.func
    parts: list[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    return ".".join(reversed(parts))


def test_wake_callback_does_not_block_or_drain_user_audio():
    callback = _wake_callback()
    calls = [_call_name(node) for node in ast.walk(callback) if isinstance(node, ast.Call)]
    assert "speaker.wait" not in calls, (
        "Wake callback must not wait for the fixed wake phrase; waiting keeps ASR "
        "in KWS mode while the user may already be asking a question."
    )
    assert "asr._audio_queue.get_nowait" not in calls, (
        "Wake callback must not drain the ASR queue after wake phrase playback; "
        "that drops the beginning of the user's question."
    )
    assert "asr._audio_queue.empty" not in calls, (
        "Wake callback must not clear ASR queue contents collected during wake handoff."
    )


def test_exact_wake_words_are_filtered_from_awake_asr_results():
    source = _main_source()
    assert "if text in WAKE_WORDS:" in source, (
        "If VAD/ASR hears the wake word tail after KWS, the exact wake word must "
        "be treated as a control word and not recorded as a child conversation."
    )
    assert "[过滤] 控制词" in source


def test_idle_sleep_resets_timer_after_requesting_sleep():
    source = _main_source()
    marker = "asr.sleep()\n                _sync_dashboard_runtime()"
    assert marker in source
    after_marker = source.split(marker, 1)[1][:180]
    assert "idle_since = time.time()" in after_marker, (
        "After requesting sleep, reset idle_since so the main loop does not print "
        "the idle sleep message repeatedly while ASR applies the async state change."
    )


if __name__ == "__main__":
    test_wake_callback_does_not_block_or_drain_user_audio()
    print("test_wake_callback_does_not_block_or_drain_user_audio PASS")
    test_exact_wake_words_are_filtered_from_awake_asr_results()
    print("test_exact_wake_words_are_filtered_from_awake_asr_results PASS")
    test_idle_sleep_resets_timer_after_requesting_sleep()
    print("test_idle_sleep_resets_timer_after_requesting_sleep PASS")
    print("ALL PASS")
