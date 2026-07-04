"""Regression tests for runtime status logging boundaries.

Run from repo root:
    python3 -m scripts.test_runtime_status_logging
"""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASR = ROOT / "asr" / "recognizer.py"
MAIN = ROOT / "main.py"
RUNTIME_LOG_FILES = [
    ROOT / "main.py",
    ROOT / "tts" / "realtime_tts.py",
    ROOT / "audio" / "aec_filter.py",
    ROOT / "dashboard" / "server.py",
    ROOT / "dashboard" / "state.py",
    ROOT / "runtime_scheduler" / "coordinator.py",
    ROOT / "safety_guard" / "service.py",
    ROOT / "safety_guard" / "monitor.py",
    ROOT / "safety_guard" / "announcer.py",
]


def _module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _function(path: Path, name: str) -> ast.FunctionDef:
    tree = _module(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{path} does not define {name}")


def _string_constants(node: ast.AST) -> list[str]:
    return [
        item.value for item in ast.walk(node)
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    ]


def _class_names(path: Path) -> set[str]:
    return {
        node.name for node in ast.walk(_module(path))
        if isinstance(node, ast.ClassDef)
    }


def test_asr_logs_mode_entry_exit_and_speaking_edges_only():
    strings = _string_constants(_module(ASR))
    required = [
        "[ASR] enter SLEEP/KWS",
        "[ASR] exit SLEEP/KWS",
        "[ASR] enter AWAKE/VAD+ASR",
        "[ASR] exit AWAKE/VAD+ASR",
        "[AWAKE] speaking:",
    ]
    for expected in required:
        assert any(expected in value for value in strings), (
            f"missing ASR runtime status log: {expected}"
        )

    run_method = _function(ASR, "_run")
    run_strings = _string_constants(run_method)
    assert not any("[AWAKE] {} chunks" in value for value in run_strings), (
        "AWAKE progress should not print every 100 chunks by default. "
        "Only mode boundaries and speaking false/true transitions should log."
    )


def test_main_reports_module_load_times_and_ready_status():
    class_names = _class_names(MAIN)
    assert "StartupProfiler" in class_names, (
        "main.py should collect per-module startup timing instead of ad-hoc prints."
    )

    strings = _string_constants(_module(MAIN))
    required = [
        "[startup] event=module",
        "[startup] event=summary",
        "[system] event=ready message=初始化完成，可以开始测试。",
    ]
    for expected in required:
        assert any(expected in value for value in strings), (
            f"missing startup status log: {expected}"
        )


def test_main_leaves_sigterm_to_process_default():
    source = MAIN.read_text(encoding="utf-8")
    assert "signal.signal(signal.SIGTERM" not in source, (
        "background stop may arrive while the main thread is blocked in audio "
        "I/O; Python-level SIGTERM handlers can delay process exit."
    )
    assert "GracefulShutdown" not in source


def test_safety_ros_camera_exits_cleanly_on_rclpy_shutdown():
    source = (ROOT / "safety_guard" / "ros_camera.py").read_text(encoding="utf-8")
    assert "SignalHandlerOptions.NO" in source, (
        "rclpy must not take over SIGTERM in cloud-model; start_system.sh uses "
        "the process default TERM behavior for reliable background stop."
    )
    assert "ExternalShutdownException" in source, (
        "rclpy raises ExternalShutdownException during process shutdown; the "
        "safety camera thread should treat it as a normal exit."
    )
    assert "except self._external_shutdown_exception" in source


def test_processing_progress_is_visible_by_default():
    strings = _string_constants(_module(MAIN))
    assert any("[处理中] loop=" in value for value in strings), (
        "processing progress should remain visible by default so manual runs show "
        "that the main loop is still active during long responses."
    )
    source = MAIN.read_text(encoding="utf-8")
    assert 'end="\\r"' not in source, (
        "carriage-refresh progress logs corrupt streaming assistant output when "
        "multiple threads write to stdout."
    )
    assert "MAIN_PROCESSING_PROGRESS_EVERY" not in source
    assert "PROCESSING_PROGRESS_EVERY" not in source


def test_recognized_text_logs_before_sleep_request():
    source = MAIN.read_text(encoding="utf-8")
    marker = "print(\"[识别] {}\".format(text), flush=True)"
    assert marker in source, (
        "recognized user text should be printed immediately in the main loop "
        "before TTS/speaker reset or background processing can delay it."
    )
    branch_start = source.index("if r and not is_processing:")
    branch_end = source.index("# 处理中定期状态", branch_start)
    branch = source[branch_start:branch_end]
    assert branch.index(marker) < branch.index("asr.sleep()"), (
        "recognized text must be visible before requesting ASR sleep."
    )


def test_tts_runtime_logs_are_structured():
    tts = ROOT / "tts" / "realtime_tts.py"
    strings = _string_constants(_module(tts))
    old_tokens = ["[合成]", "[合成错误]", "[TTS实时重试", "[固定语录", "[固定语录错误]"]
    for token in old_tokens:
        assert not any(token in value for value in strings), (
            f"TTS runtime log still uses old format: {token}"
        )
    required = [
        "[tts.realtime] event=retry",
        "[tts.synth] event=sentence_done",
        "[tts.synth] event=sentence_error",
        "[tts.phrase] event=ready",
        "[tts.phrase] event=error",
    ]
    for expected in required:
        assert any(expected in value for value in strings), (
            f"missing structured TTS log: {expected}"
        )


def test_runtime_log_prefixes_are_consistent():
    protected = [
        "[ASR]",
        "[AWAKE]",
        "[KWS]",
        "[识别]",
        "[处理中]",
        "[过滤]",
    ]
    allowed = protected + [
        "[startup]",
        "[system]",
        "[main]",
        "[wake]",
        "[interrupt]",
        "[reading]",
        "[dashboard]",
        "[person_task]",
        "[scheduler]",
        "[safety]",
        "[tts.",
        "[tts]",
        "[aec]",
    ]
    blocked = [
        "[启动]",
        "[系统]",
        "[_on_wake]",
        "[主循环]",
        "[处理线程]",
        "[自动唤醒]",
        "[休眠]",
        "[读书模式]",
        "[打断]",
        "[Dashboard]",
        "[Scheduler]",
        "[SafetyGuard]",
        "[SafetyMonitor]",
        "[SafetyAnnouncer]",
        "[AEC]",
        "[WAV增益错误]",
        "[AEC feed错误]",
        "[wake_reply]",
    ]
    strings = []
    for path in RUNTIME_LOG_FILES:
        strings.extend(_string_constants(_module(path)))

    for token in blocked:
        assert not any(token in value for value in strings), (
            f"runtime log still uses legacy/mixed prefix: {token}"
        )
    for value in strings:
        stripped = value.lstrip()
        if not stripped.startswith("["):
            continue
        if any(stripped.startswith(prefix) for prefix in allowed):
            continue
        # Skip non-log strings such as regex char classes and voice tags.
        if stripped.startswith("[a-z") or stripped.startswith("[VoiceName]"):
            continue
        raise AssertionError(f"unexpected runtime log prefix: {value!r}")


def test_tts_health_script_exists():
    script = ROOT / "scripts" / "check_tts_health.py"
    assert script.exists(), "missing TTS health check script"
    text = script.read_text(encoding="utf-8")
    assert "TTS_HEALTH" in text
    assert "RealtimeTTSSession" in text


if __name__ == "__main__":
    test_asr_logs_mode_entry_exit_and_speaking_edges_only()
    print("test_asr_logs_mode_entry_exit_and_speaking_edges_only PASS")
    test_main_reports_module_load_times_and_ready_status()
    print("test_main_reports_module_load_times_and_ready_status PASS")
    test_main_leaves_sigterm_to_process_default()
    print("test_main_leaves_sigterm_to_process_default PASS")
    test_safety_ros_camera_exits_cleanly_on_rclpy_shutdown()
    print("test_safety_ros_camera_exits_cleanly_on_rclpy_shutdown PASS")
    test_processing_progress_is_visible_by_default()
    print("test_processing_progress_is_visible_by_default PASS")
    test_recognized_text_logs_before_sleep_request()
    print("test_recognized_text_logs_before_sleep_request PASS")
    test_tts_runtime_logs_are_structured()
    print("test_tts_runtime_logs_are_structured PASS")
    test_runtime_log_prefixes_are_consistent()
    print("test_runtime_log_prefixes_are_consistent PASS")
    test_tts_health_script_exists()
    print("test_tts_health_script_exists PASS")
    print("ALL PASS")
