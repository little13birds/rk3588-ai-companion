"""Regression tests for KWS interrupt word routing.

Run from repo root:
    python3 -m scripts.test_interrupt_words
"""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"


def _interrupt_words() -> set[str]:
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    words: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "INTERRUPT_WORDS"
                   for target in node.targets):
            continue
        if isinstance(node.value, ast.Set):
            for item in node.value.elts:
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    words.add(item.value)
    return words


def test_single_stop_is_not_interrupt_word():
    words = _interrupt_words()
    assert "停" not in words


def test_multi_character_stop_phrases_still_interrupt():
    words = _interrupt_words()
    for phrase in {"停一下", "停一停", "停止", "暂停"}:
        assert phrase in words


if __name__ == "__main__":
    test_single_stop_is_not_interrupt_word()
    print("test_single_stop_is_not_interrupt_word PASS")
    test_multi_character_stop_phrases_still_interrupt()
    print("test_multi_character_stop_phrases_still_interrupt PASS")
    print("ALL PASS")
