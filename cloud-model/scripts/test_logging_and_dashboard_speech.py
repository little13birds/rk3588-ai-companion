"""Regression tests for quiet KWS logs and dashboard speech playback setup.

Run from repo root:
    python3 -m scripts.test_logging_and_dashboard_speech
"""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASR = ROOT / "asr" / "recognizer.py"
MAIN = ROOT / "main.py"


def _function(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{path} does not define {name}")


def _call_name(node: ast.Call) -> str:
    func = node.func
    parts: list[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    return ".".join(reversed(parts))


def _string_constants(node: ast.AST) -> list[str]:
    return [
        item.value for item in ast.walk(node)
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    ]


def _function_source(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError(f"{path} does not define {name}")


def test_kws_periodic_progress_log_is_not_hardcoded():
    run_method = _function(ASR, "_run")
    strings = _string_constants(run_method)
    assert not any("[KWS] 已处理{}块" in value for value in strings), (
        "KWS periodic progress logs should be disabled by default or gated behind "
        "configuration; they should not spam stdout every 50 chunks."
    )


def test_dashboard_speech_resets_speaker_before_queueing():
    process = _function(MAIN, "process_dashboard_speech")
    calls = [_call_name(node) for node in ast.walk(process) if isinstance(node, ast.Call)]
    assert "speaker.reset" in calls, (
        "Dashboard sleep reminders must reset RealtimeSpeaker before feed/flush. "
        "A previous interrupt leaves cancel_flag set, otherwise the TTS synth loop "
        "can discard the reminder without playback."
    )
    assert calls.index("speaker.reset") < calls.index("speaker.feed"), (
        "speaker.reset() must happen before speaker.feed() in dashboard speech."
    )


def test_dashboard_parent_speech_is_not_duplicated_as_robot_conversation():
    source = _function_source(MAIN, "process_dashboard_speech")
    assert 'source not in {"parent", "sleep_remind"}' in source, (
        "Parent dashboard speech and sleep reminders are already recorded when "
        "queued. process_dashboard_speech must not add a duplicate robot "
        "conversation bubble for those sources."
    )


if __name__ == "__main__":
    test_kws_periodic_progress_log_is_not_hardcoded()
    print("test_kws_periodic_progress_log_is_not_hardcoded PASS")
    test_dashboard_speech_resets_speaker_before_queueing()
    print("test_dashboard_speech_resets_speaker_before_queueing PASS")
    test_dashboard_parent_speech_is_not_duplicated_as_robot_conversation()
    print("test_dashboard_parent_speech_is_not_duplicated_as_robot_conversation PASS")
    print("ALL PASS")
