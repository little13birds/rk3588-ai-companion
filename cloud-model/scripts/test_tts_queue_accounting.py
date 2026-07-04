"""Regression tests for RealtimeSpeaker queue task accounting.

Run from repo root:
    python3 -m scripts.test_tts_queue_accounting
"""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REALTIME_TTS = ROOT / "tts" / "realtime_tts.py"


def _method(class_name: str, method_name: str) -> ast.FunctionDef:
    tree = ast.parse(REALTIME_TTS.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    return item
    raise AssertionError(f"{class_name}.{method_name} not found")


def _call_name(node: ast.Call) -> str:
    func = node.func
    parts: list[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    return ".".join(reversed(parts))


def _inside_finally(root: ast.AST, target: ast.AST) -> bool:
    for node in ast.walk(root):
        if not isinstance(node, ast.Try):
            continue
        for finalizer in node.finalbody:
            if any(child is target for child in ast.walk(finalizer)):
                return True
    return False


def test_synth_loop_marks_sentence_queue_done_only_once_per_item():
    method = _method("RealtimeSpeaker", "_synth_loop")
    sentence_task_done_calls = [
        node for node in ast.walk(method)
        if isinstance(node, ast.Call) and _call_name(node) == "self._sentence_queue.task_done"
    ]
    assert sentence_task_done_calls, "_synth_loop must mark sentence queue items done"
    non_finally_calls = [
        node for node in sentence_task_done_calls
        if not _inside_finally(method, node)
    ]
    assert not non_finally_calls, (
        "_synth_loop must call self._sentence_queue.task_done() only from the "
        "single finally block. Calling it before continue and again in finally "
        "can crash with ValueError: task_done() called too many times during cancel."
    )
    assert len(sentence_task_done_calls) == 1, (
        "_synth_loop should have exactly one sentence_queue.task_done() call."
    )


def test_play_loop_marks_audio_queue_done_only_once_per_item():
    method = _method("RealtimeSpeaker", "_play_loop")
    audio_task_done_calls = [
        node for node in ast.walk(method)
        if isinstance(node, ast.Call) and _call_name(node) == "self._audio_queue.task_done"
    ]
    assert audio_task_done_calls, "_play_loop must mark audio queue items done"
    non_finally_calls = [
        node for node in audio_task_done_calls
        if not _inside_finally(method, node)
    ]
    assert not non_finally_calls, (
        "_play_loop must call self._audio_queue.task_done() only from one finally "
        "block. cancel() can clear the audio queue while playback is active, so "
        "branch-local task_done calls are fragile."
    )
    assert len(audio_task_done_calls) == 1, (
        "_play_loop should have exactly one audio_queue.task_done() call."
    )


if __name__ == "__main__":
    test_synth_loop_marks_sentence_queue_done_only_once_per_item()
    print("test_synth_loop_marks_sentence_queue_done_only_once_per_item PASS")
    test_play_loop_marks_audio_queue_done_only_once_per_item()
    print("test_play_loop_marks_audio_queue_done_only_once_per_item PASS")
    print("ALL PASS")
