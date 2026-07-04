"""AI 语音助手 — ASR → VLM → TTS 全流程 + Function Calling + 打断功能"""
import sys, os, time, subprocess, queue, numpy as np, threading, select, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import SAMPLE_RATE, DEVICE_MIC
from asr.recognizer import ASRProcessor
from llm.chat import Conversation
from tts.realtime_tts import RealtimeSpeaker as StreamSpeaker
from audio.aec_filter import SharedAecFilter
from arm import agent_client
from book_match_client import BookMatchClient
from dashboard import DashboardState, start_dashboard_server
from dashboard.chassis_control import ChassisControlAdapter
from display.eye_controller import EyeDisplayController
from reading_mode import classify_reading_turn
from runtime_scheduler import RuntimeCoordinator
from safety_guard import SafetyGuardConfig, SafetyGuardService
from person_tasks import PERSON_TASK_TOOLS, PersonTaskController, execute_person_tool, parse_person_task_intent
from utils.console_io import console_print

CHUNK_BYTES = 320
IDLE_SLEEP_SEC = 5


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# 噪声过滤：这些纯标点/极短词是 VAD 误触发产生的，直接丢弃
import re as _re
_NOISE_PATTERNS = [
    # 纯标点/空白
    (r"^[\.。,，!！?？;；:：\s\-—…~～\'\"\+]+$", "纯标点"),
    # 极短英文（常见语气词）
    (r"^(?i:yeah|yes|no|ok|oh|ah|um|uh|eh|mm|hm|ha|hey|hi|yo|wow|hmm|mhm|shh)[\.!！?？\s]*$", "短语气词"),
    # 极短中文（1 个字或纯语气）
    (r"^[嗯啊哦哎唉诶嘿喂哼哈呀呢嘛吧]([\.。!！?？\s]*)$", "短语气字"),
    # 单个日文假名（噪声，如 "い" "あ"）
    (r"^[぀-ゟ゠-ヿ]{1,3}([\.。!！?？\s]*)$", "日文假名"),
    # 1-3字母极短英文词（噪声，如 "The." "An." "It." "a"）
    (r"^[a-zA-Z]{1,3}[\.!！?？\s]*$", "极短英文词"),
]

def _is_noise(text: str) -> bool:
    """判断 ASR 结果是否为噪声/误识别，不应触发对话"""
    stripped = text.strip()
    if not stripped:
        return True
    # 长度 < 2 且无中文字 → 噪声
    if len(stripped) < 2:
        return True
    for pattern, _label in _NOISE_PATTERNS:
        if _re.match(pattern, stripped):
            return True
    return False


class StartupProfiler:
    """Collect and print coarse module startup timing for board-side debugging."""

    def __init__(self):
        self.started_at = time.perf_counter()
        self.records = []

    def start(self) -> float:
        return time.perf_counter()

    def record(self, name: str, started_at: float,
               status: str = "ok", detail: str = ""):
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        self.records.append((name, status, elapsed_ms, detail))
        suffix = " detail={}".format(detail) if detail else ""
        print("[startup] event=module name={} status={} elapsed_ms={:.1f}{}".format(
            name, status, elapsed_ms, suffix), flush=True)

    def summary(self):
        total_ms = (time.perf_counter() - self.started_at) * 1000.0
        print("[startup] event=summary", flush=True)
        for name, status, elapsed_ms, detail in self.records:
            suffix = " detail={}".format(detail) if detail else ""
            print("[startup] event=module_summary name={} status={} elapsed_ms={:.1f}{}".format(
                name, status, elapsed_ms, suffix), flush=True)
        print("[startup] event=total elapsed_ms={:.1f}".format(total_ms), flush=True)

TOOLS = [{
    "type": "function",
    "function": {
        "name": "take_photo",
        "description": (
            "拍摄一张照片，查看机器人面前真实世界的东西。"
            "仅在以下情况调用：用户问'面前/前面/周围有什么/看到什么/看看'。"
            "不要调用：讲故事、闲聊、知识问答、代码、创作内容、亮度、光照、"
            "运动、速度、倾斜、以及不需要摄像头就能回答的问题。"
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}, {
    "type": "function",
    "function": {
        "name": "get_brightness",
        "description": (
            "读取环境光照强度传感器（GY-30），返回 lux 数值。"
            "仅在以下情况调用：用户明确问'亮度''光照''光线''有多亮''光线够不够'。"
            "不要调用：与光亮/光线/环境亮度无关的任何其他问题。"
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}, {
    "type": "function",
    "function": {
        "name": "get_motion",
        "description": (
            "读取设备运动传感器（MPU6050），返回加速度和角速度数据。"
            "仅在以下情况调用：用户明确问'在动吗''速度''运动''倾斜''晃''震动'。"
            "不要调用：与运动/速度/姿态无关的任何其他问题。"
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}, {
    "type": "function",
    "function": {
        "name": "get_temperature",
        "description": (
            "读取环境温湿度传感器（DHT11），只能返回数值，无法改变温度。"
            "仅在以下情况调用：用户明确问'温度''气温''热不热''冷''湿度''潮湿'。"
            "调用后只报告读数，不要说'帮你调温度''帮你开空调'之类的话。"
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}]

TOOLS = TOOLS + PERSON_TASK_TOOLS

SYSTEM_PROMPT = (
    "你是小智，一个能听会看的机器人助手。你能通过麦克风听到用户说话。"
    "你的主要交流对象是3-6岁的儿童，说话要像温柔的哥哥姐姐一样，短句、简单、耐心、亲切。"
    "工具：摄像头(take_photo)看东西、光照传感器(get_brightness)测亮度、"
    "运动传感器(get_motion)感知运动和姿态、温湿度传感器(get_temperature)只能测量并报告温湿度数值，"
    "人物任务工具(control_person_follow)可以控制机器人找人或跟随人："
    "用户说'跟着我/跟我走/跟我来'时跟随 nearest；"
    "用户说'跟着角色A'或'A'代表 tao，'角色B'或'B'代表 xiao；"
    "用户问'你知道我是谁吗/前面都有谁'时调用 observe_people_identity 查询身份数据库结果。"
    "你无法控制空调、无法调节温度、无法开关窗户。用户问你热不热，你只读数回答数值，不要说'帮你调温度'之类的话。"
    "仅当用户明确询问相关话题时才调用对应工具，其他话题直接回复不调工具。"
    "每次回复1-3句话，简洁明了。不寒暄不啰嗦。"
    "和用户对话时用自然的口语，像一个朋友一样。"
    "面对小朋友时，优先用孩子听得懂的生活例子解释，不说复杂术语，不用命令式或责备式语气。"
    "如果孩子说得不清楚、像咿呀乱语、只有几个词或前后不连贯，不要假装听懂，也不要乱编答案；"
    "要温柔地请孩子再说一遍，或给出简单选项让孩子选择。"
    "如果孩子表达害怕、难过、疼痛或危险，先安抚孩子，再提醒他去找爸爸妈妈或身边的大人。"
    "输出纯文本，禁止使用任何Markdown格式符号（如**加粗**、#标题、*斜体）、emoji表情或装饰性字符。"
)

STORY_KW = ["讲故事", "讲个故事", "讲一个故事", "说个故事", "说故事",
            "讲童话", "讲个童话", "编故事", "编个故事", "来段故事"]

READING_KW = ["读书模式", "阅读模式", "读一下书", "读读书", "帮我读书", "念书", "念一下",
              "读一读", "帮我读", "给我读"]

READING_SYSTEM_PROMPT = (
    "你是小智，一个能帮用户读书的OCR朗读器。你已进入持续读书模式。"
    "你现在是在陪3-6岁小朋友读书，语气要温柔、慢一点、像哥哥姐姐给孩子读绘本。"
    "用户会请你读书上的内容，但你一开始看不到书本。"
    "你需要先用 take_photo 拍一张照片来看用户面前的书。"
    "如果 take_photo 返回【逐字朗读以下内容】，说明数据库已经匹配到页面原文，"
    "你必须只朗读其中给出的原文，不要再根据照片猜测。"
    "看到照片后："
    "- 如果照片里没有文字，或者文字模糊、太小、反光导致看不清："
    "  直接告诉用户看不清，请用户调整角度或距离，然后询问'需要再试一次吗？'"
    "  表达要温和，例如'小智有点看不清，可以把书放正一点吗？'"
    "- 如果照片里的文字清晰："
    "  按照原文顺序逐字逐句朗读，不加解释、不总结、不评论、不增减内容。"
    "即使语气温柔，也必须严格按照书上的原文朗读；不要自己改写、解释、总结或发挥。"
    "朗读完成后，必须在最后一句说\"需要继续读下一页吗？\"，不可省略。"
    "最后一句也可以说\"这一页读完啦，还要继续读下一页吗？\"，但必须包含继续读下一页的询问。"
    "如果用户表示继续，继续拍照朗读。"
    "如果用户表示不读了，简单告别并结束。"
    "规则：不寒暄不啰嗦，直接做事。但翻页询问必须说。"
    "输出纯文本，禁止使用任何Markdown格式符号（如**加粗**、#标题、*斜体）、emoji表情或装饰性字符。"
)

READING_TOOLS = [TOOLS[0]]  # 只保留 take_photo

READING_EXIT_KW = ["退出读书模式", "结束读书", "退出阅读", "不读了"]
READING_CONTINUE_KW = ["继续", "继续读", "继续读书", "下一页", "下页", "读下一页", "读下一张",
                       "再读一页", "接着读", "翻页", "再试一次", "再拍一次", "重新拍", "重拍",
                       "再来一次"]
READING_CHAT_EXIT_REPLY = "好的，先不读书了，我们聊天吧。"


def _normalized_command(text: str) -> str:
    return "".join((text or "").split())


def is_reading_continue_request(text: str) -> bool:
    normalized = _normalized_command(text)
    return bool(normalized and any(kw in normalized for kw in READING_CONTINUE_KW))


def is_reading_chat_request(text: str) -> bool:
    normalized = _normalized_command(text)
    if not normalized:
        return False
    if any(kw in normalized for kw in READING_EXIT_KW):
        return False
    if is_reading_continue_request(normalized):
        return False
    if any(kw in normalized for kw in READING_KW):
        return False
    return True


def start_mic(retries=5):
    for i in range(retries):
        p = subprocess.Popen(
            ["arecord", "-q", "-D", DEVICE_MIC,
             "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        time.sleep(0.1)
        if p.poll() is None:
            return p
        p.terminate()
        time.sleep(0.5)
    raise RuntimeError("麦克风无法打开")


def drain_mic(mic_stdout, deadline_sec: float = 1.0, label: str = "") -> int:
    """清空 mic 管道残留，返回清掉的块数"""
    deadline = time.time() + deadline_sec
    drained = 0
    while time.time() < deadline:
        try:
            ready, _, _ = select.select([mic_stdout], [], [], 0.1)
        except Exception:
            break
        if ready:
            try:
                mic_stdout.read(CHUNK_BYTES * 10)
                drained += 1
            except Exception:
                break
        else:
            break
    if label:
        print("[main] event=mic_drain label={} chunks={}".format(label, drained), flush=True)
    return drained


def main():
    print("=" * 55)
    print("AI 语音助手 — Function Calling + 打断版")
    print("唤醒词: 你好小智 / 小智小智 | {}秒无对话回休眠".format(IDLE_SLEEP_SEC))
    print("试试说: 你面前有什么？")
    print("=" * 55)

    startup = StartupProfiler()

    started = startup.start()
    aec = SharedAecFilter(sample_rate=SAMPLE_RATE, channels=1)
    startup.record("aec_filter", started)

    started = startup.start()
    speaker = StreamSpeaker(voice="Cherry", aec_filter=aec)
    startup.record("tts_speaker", started)

    # ── Book matching engine ─────────────────────────────────
    DB_DIR = os.path.expanduser("~/Desktop/database_tokenize_match/database")
    MDL_DIR = os.path.expanduser("~/Desktop/database_tokenize_match/models")
    book_match_client = None
    started = startup.start()
    book_match_status = "ok"
    book_match_detail = ""
    try:
        print(
            "[main] event=book_match_init db={} db_exists={} models={} models_exists={}".format(
                DB_DIR,
                os.path.isdir(DB_DIR),
                MDL_DIR,
                os.path.isdir(MDL_DIR),
            ),
            flush=True,
        )
        book_match_client = BookMatchClient(DB_DIR, MDL_DIR)
    except Exception as e:
        book_match_status = "disabled"
        book_match_detail = type(e).__name__
        print("[main] event=book_match_init_failed error_type={} error={}".format(
            type(e).__name__, e), flush=True)
    startup.record("book_match", started, book_match_status, book_match_detail)

    started = startup.start()
    person_task_controller = PersonTaskController()
    conv = Conversation(
        system_prompt=SYSTEM_PROMPT,
        summary_interval=3, keep_recent=2,
        max_tokens=200, speaker=speaker,
        tools=TOOLS,
        book_match=book_match_client,
        person_task_controller=person_task_controller,
        on_stream_start=lambda: eye_display.set_mode("speaking"),
    )
    startup.record("conversation", started)

    started = startup.start()
    from audio.fillers import (
        wake_reply, think_filler, set_speaker, reading_in_filler,
        reading_next_page_filler, reading_retry_filler,
        reading_out_filler, reading_continue_filler,
    )
    set_speaker(speaker)
    startup.record("fixed_phrases", started)

    started = startup.start()
    eye_display = EyeDisplayController.from_env()
    eye_started = eye_display.start()
    eye_status = "ok" if eye_started or not eye_display.enabled else "degraded"
    startup.record("eye_display", started, eye_status)

    result_queue = queue.Queue()
    person_event_queue = queue.Queue()
    person_task_controller.set_event_handler(lambda event: person_event_queue.put(event))

    # ── 共享状态 ──
    is_processing = False
    cancel_event = threading.Event()
    interrupt_requested = False
    interrupt_reason = None
    pending_wake_reply = False
    auto_awake_requested = False
    active_person_task = None
    last_reading_success = None
    idle_since = time.time()
    mic = None  # 延迟初始化
    MODE = "normal"  # "normal" | "reading" | "story"
    safety_guard = None
    runtime_coordinator = None
    dashboard_server = None
    chassis_control = None
    sleep_presence_stop = threading.Event()
    sleep_presence_thread = None
    started = startup.start()
    dashboard_state = DashboardState.from_env()
    dashboard_state.set_person_task_controller(person_task_controller)
    chassis_control = ChassisControlAdapter.from_env()
    dashboard_state.set_move_handler(
        chassis_control.handle_dashboard_command,
        chassis_control.status,
    )
    dashboard_state.set_runtime(mode=MODE, is_processing=is_processing, is_awake=False)
    dashboard_server = start_dashboard_server(dashboard_state)
    startup.record("dashboard", started)

    started = startup.start()
    chassis_support_status = "skipped"
    chassis_support_detail = "dashboard chassis control disabled"
    if chassis_control.config.enabled:
        try:
            person_task_controller.ensure_chassis_support_stack()
            chassis_support_status = "ok"
            chassis_support_detail = "support stack requested"
            print("[chassis] event=support_stack_ready", flush=True)
        except Exception as exc:
            chassis_support_status = "error"
            chassis_support_detail = "{}: {}".format(type(exc).__name__, exc)
            print("[chassis] event=support_stack_failed error_type={} error={}".format(
                type(exc).__name__, exc), flush=True)
    startup.record("chassis_support", started, chassis_support_status, chassis_support_detail)

    # 唤醒词: SLEEP时唤醒；读书模式处理中允许暂停当前页
    WAKE_WORDS = {"你好小智", "小智小智"}
    # 打断词: 处理中打断, SLEEP时忽略(不唤醒)
    INTERRUPT_WORDS = {"停一下", "停一停", "停止", "暂停", "安静", "别说", "先别说"}

    def _play_wake_reply_async():
        def _run():
            try:
                wake_reply()
            except Exception as e:
                print("[wake] event=reply_failed error={}".format(e), flush=True)

        threading.Thread(target=_run, name="wake-reply", daemon=True).start()

    def _on_wake(kw):
        nonlocal idle_since, interrupt_requested, interrupt_reason, pending_wake_reply
        idle_since = time.time()
        print("[wake] event=detected kw={} processing={} awake={}".format(
            kw, is_processing, asr.is_awake), flush=True)
        if is_processing:
            if MODE == "reading" and kw in WAKE_WORDS:
                print("\n>>> 暂停读书: {} <<<".format(kw), flush=True)
                eye_display.blink()
                eye_display.set_mode("listen")
                interrupt_reason = "reading_pause"
                pending_wake_reply = True
                interrupt_requested = True
                cancel_event.set()
            elif kw in INTERRUPT_WORDS:
                print("\n>>> 打断: {} <<<".format(kw), flush=True)
                eye_display.blink()
                interrupt_reason = "hard_stop"
                interrupt_requested = True
                cancel_event.set()
            else:
                print("[wake] event=ignored reason=processing kw={}".format(kw), flush=True)
        else:
            if kw in WAKE_WORDS:
                asr._request_state(True)
                eye_display.set_mode("listen")
                _play_wake_reply_async()
                _sync_dashboard_runtime()
                print("\n>>> 唤醒: {} <<<".format(kw), flush=True)
            else:
                print("[wake] event=ignored reason=sleep kw={}".format(kw), flush=True)
    def _on_vad(speaking):
        nonlocal idle_since
        idle_since = time.time()
        if speaking:
            eye_display.set_mode("listen")

    def _sync_dashboard_runtime():
        dashboard_state.set_runtime(
            mode=MODE,
            is_processing=is_processing,
            is_awake=getattr(asr, "is_awake", False),
        )

    started = startup.start()
    asr = ASRProcessor(
        silence_timeout_ms=800,
        on_result=lambda r: result_queue.put(r),
        on_wake=_on_wake,
        on_vad=_on_vad,
    )
    startup.record("asr_models", started)

    started = startup.start()
    asr.start()
    startup.record("asr_worker", started)

    started = startup.start()
    mic = start_mic()
    startup.record("microphone", started)

    started = startup.start()
    safety_guard = SafetyGuardService(
        config=SafetyGuardConfig.from_env(),
        speaker=speaker,
        cancel_event=cancel_event,
    )
    safety_guard.start()
    person_task_controller.set_snapshot_provider(safety_guard.camera_snapshot)
    from vision.camera import set_snapshot_provider
    set_snapshot_provider(safety_guard.camera_snapshot)
    startup.record("safety_guard", started)

    started = startup.start()
    runtime_coordinator = RuntimeCoordinator.from_env(safety_guard=safety_guard)
    runtime_coordinator.bootstrap()
    dashboard_state.set_camera_snapshot_provider(safety_guard.camera_snapshot)
    dashboard_state.set_reading_camera_snapshot_provider(
        lambda: agent_client.get_frame(wait_ready=False, timeout=1.0)
    )
    dashboard_state.set_scheduler_status_provider(runtime_coordinator.snapshot)
    startup.record("runtime_scheduler", started)

    def _sleep_presence_loop():
        interval = max(1.0, _float_env("DASHBOARD_SLEEP_PRESENCE_INTERVAL_SEC", 3.0))
        print("[sleep_presence] event=started interval_sec={:.1f}".format(interval), flush=True)
        while not sleep_presence_stop.wait(interval):
            try:
                result = dashboard_state.refresh_sleep_presence_from_identity()
                if result.get("visible_children"):
                    print("[sleep_presence] event=visible children={}".format(
                        ",".join(result.get("visible_children") or [])), flush=True)
                elif result.get("error"):
                    print("[sleep_presence] event=error error={}".format(
                        result.get("error")), flush=True)
            except Exception as exc:
                print("[sleep_presence] event=failed error_type={} error={}".format(
                    type(exc).__name__, exc), flush=True)
        print("[sleep_presence] event=stopped", flush=True)

    if _bool_env("DASHBOARD_SLEEP_PRESENCE_ENABLED", True):
        sleep_presence_thread = threading.Thread(
            target=_sleep_presence_loop,
            name="sleep-presence",
            daemon=True,
        )
        sleep_presence_thread.start()
    else:
        print("[sleep_presence] event=disabled", flush=True)

    startup.summary()
    print("[system] event=ready message=初始化完成，可以开始测试。", flush=True)

    def _start_reading_tracking() -> bool:
        if runtime_coordinator is not None:
            return runtime_coordinator.start_reading()
        return agent_client.start_reading()

    def _stop_reading_tracking(return_home: bool = False) -> bool:
        if runtime_coordinator is not None:
            if not return_home:
                return runtime_coordinator.pause_reading_page()
            return runtime_coordinator.stop_reading(return_home=return_home)
        return agent_client.stop_reading(return_home=return_home)

    def _scheduler_enabled() -> bool:
        return bool(runtime_coordinator is not None and runtime_coordinator.scheduler.enabled)

    def _stop_person_tasks(reason: str) -> dict | None:
        try:
            result = person_task_controller.control("stop", "nearest")
            print("[person_task] event=shutdown_cleanup reason={} result={}".format(
                reason, json.dumps(result, ensure_ascii=False, sort_keys=True)), flush=True)
            return result
        except Exception as exc:
            print("[person_task] event=shutdown_cleanup_failed reason={} error_type={} error={}".format(
                reason, type(exc).__name__, exc), flush=True)
            return None

    def _person_task_stop_texts(task: dict | None) -> tuple[str, str]:
        action = str((task or {}).get("action") or "")
        if action == "seek":
            return "正在停止寻找，请稍候。", "已停止寻找。"
        return "正在停止跟随，请稍候。", "已停止跟随。"

    def process_utterance(text: str):
        """后台线程：处理用户语音"""
        nonlocal is_processing, auto_awake_requested, MODE, interrupt_reason, active_person_task, last_reading_success

        # 确保 speaker 和 AEC 处于干净状态
        speaker.reset()
        aec.reset()
        cancel_event.clear()
        interrupt_reason = None

        dashboard_state.add_conversation("child", text)
        dashboard_state.add_activity("info", "孩子发起语音交互")
        eye_display.set_mode("thinking")
        _sync_dashboard_runtime()

        is_story = any(kw in text for kw in STORY_KW)
        is_reading_entry = any(kw in text for kw in READING_KW)
        is_reading_continue = (MODE == "reading" and is_reading_continue_request(text))
        is_reading_exit = (MODE == "reading"
                           and any(kw in text for kw in READING_EXIT_KW))
        reading_chat_transition = (
            MODE == "reading"
            and is_reading_chat_request(text)
            and not is_reading_continue
        )
        person_intent = parse_person_task_intent(text)
        immediate_filler_played = False

        # ── 读书模式退出 ──
        if is_reading_exit:
            conv.max_tokens = 200
            conv.tools = TOOLS
            speaker.speed = 1.0
            conv.messages[0] = {"role": "system", "content": SYSTEM_PROMPT}
            last_reading_success = None
            MODE = "normal"
            dashboard_state.set_runtime(mode=MODE, is_processing=True)
            cancel_event.clear()
            interrupt_reason = None
            eye_display.set_mode("speaking")
            speaker.feed("正在退出读书模式，请稍候。")
            speaker.flush()
            eye_display.set_mode("thinking")
            _stop_reading_tracking(return_home=True)    # 退出读书模式，回初始位
            cancel_event.clear()
            interrupt_reason = None
            eye_display.set_mode("speaking")
            speaker.feed("已退出读书模式。")
            speaker.flush()
            speaker.wait()
            eye_display.set_mode("sleep")
            dashboard_state.set_runtime(mode=MODE, is_processing=False)
            print("[reading] event=exit reason=keyword mode=normal", flush=True)
            is_processing = False
            auto_awake_requested = False
            asr.sleep()
            _sync_dashboard_runtime()
            return

        if reading_chat_transition:
            think_filler()
            immediate_filler_played = True
            conv.max_tokens = 200
            conv.tools = TOOLS
            speaker.speed = 1.0
            conv.messages[0] = {"role": "system", "content": SYSTEM_PROMPT}
            last_reading_success = None
            MODE = "normal"
            dashboard_state.set_runtime(mode=MODE, is_processing=True)
            cancel_event.clear()
            interrupt_reason = None
            eye_display.set_mode("thinking")
            _stop_reading_tracking(return_home=True)
            cancel_event.clear()
            interrupt_reason = None
            eye_display.set_mode("speaking")
            speaker.feed(READING_CHAT_EXIT_REPLY)
            speaker.flush()
            speaker.wait()
            dashboard_state.add_conversation(
                "robot", READING_CHAT_EXIT_REPLY, source="reading_transition")
            dashboard_state.add_activity(
                "system",
                "退出读书模式，转入普通对话",
                kind="system",
                actor="robot",
                title="读书模式",
            )
            print("[reading] event=exit reason=chat_request mode=normal", flush=True)
            is_reading_exit = False
            is_reading_entry = False
            is_reading_continue = False

        if (
            person_intent
            and person_intent.get("tool") == "control_person_follow"
            and MODE != "reading"
        ):
            requested_args = person_intent.get("args", {})
            requested_action = str(requested_args.get("action") or "").strip().lower()
            previous_person_task = active_person_task
            if requested_action == "stop":
                progress_text, stop_done_text = _person_task_stop_texts(previous_person_task)
            elif requested_action == "seek":
                progress_text = "正在寻找目标，请稍候。"
                stop_done_text = "已停止跟随。"
            else:
                progress_text = "正在启动跟随，请稍候。"
                stop_done_text = "已停止跟随。"
            if requested_action in {"follow", "seek"}:
                eye_display.set_mode("following")
            else:
                eye_display.set_mode("thinking")
            eye_display.set_mode("speaking")
            speaker.feed(progress_text)
            speaker.flush()
            if requested_action in {"follow", "seek"}:
                eye_display.set_mode("following")
            else:
                eye_display.set_mode("thinking")
            result = execute_person_tool(
                "control_person_follow",
                json.dumps(requested_args, ensure_ascii=False),
                controller=person_task_controller,
            )
            try:
                payload = json.loads(result[0].get("text", "{}"))
            except Exception:
                payload = {"ok": False, "error": "bad_tool_payload"}
            action = payload.get("action")
            target = payload.get("target_name") or payload.get("target") or ""
            if payload.get("ok"):
                if action == "follow":
                    reply = "已开始跟随。"
                    active_person_task = {"action": "follow", "target": target}
                elif action == "seek":
                    reply = "已开始寻找。"
                    active_person_task = {"action": "seek", "target": target}
                elif action == "stop":
                    reply = stop_done_text
                    active_person_task = None
                else:
                    reply = "好的。"
                print("[person_task] event=direct_intent action={} target={}".format(
                    action, target), flush=True)
            else:
                reply = "人物跟随功能没有准备好，请稍后再试。"
                print("[person_task] event=direct_intent_failed payload={}".format(
                    payload), flush=True)
            eye_display.set_mode("speaking")
            speaker.feed(reply)
            speaker.flush()
            dashboard_state.add_conversation("robot", reply, source="person_task")
            dashboard_state.add_activity(
                "system",
                "人物任务: {} {}".format(action or "unknown", target),
                kind="system",
                actor="robot",
                title="人物任务",
                meta=payload,
            )
            is_processing = False
            auto_awake_requested = False
            asr.sleep()
            eye_display.set_mode("sleep")
            _sync_dashboard_runtime()
            return

        time.sleep(0.3)
        if not immediate_filler_played and not (is_reading_entry or is_reading_continue or MODE == "reading"):
            think_filler()

        # ── 保存当前状态（用于退出时恢复）──
        saved_max_tokens = conv.max_tokens
        saved_tools = conv.tools
        saved_speed = speaker.speed
        saved_system = conv.messages[0]["content"] if conv.messages else ""

        # ── 模式切换 ──
        if is_reading_entry:
            think_filler()
            eye_display.set_mode("thinking")
            if active_person_task:
                stop_begin, stop_done = _person_task_stop_texts(active_person_task)
                speaker.feed(stop_begin)
                speaker.flush()
                _stop_person_tasks("before_reading")
                active_person_task = None
                speaker.feed(stop_done)
                speaker.flush()
                speaker.wait()
            else:
                _stop_person_tasks("before_reading")
            eye_display.set_mode("speaking")
            speaker.feed("正在进入读书模式，请稍候。")
            speaker.flush()
            eye_display.set_mode("thinking")
            reading_tracking_ok = _start_reading_tracking()   # 通知机械臂开始视觉跟踪书本
            if _scheduler_enabled() and not reading_tracking_ok:
                dashboard_state.add_activity("warn", "读书模式启动失败: arm_agent 未就绪")
                eye_display.set_mode("speaking")
                speaker.feed("读书摄像头还没准备好，请检查机械臂服务。")
                speaker.flush()
                speaker.wait()
                is_processing = False
                auto_awake_requested = False
                asr.sleep()
                eye_display.set_mode("sleep")
                _sync_dashboard_runtime()
                return
            conv.messages[0] = {
                "role": "system",
                "content": READING_SYSTEM_PROMPT,
            }
            conv.max_tokens = 800
            conv.tools = READING_TOOLS
            speaker.speed = 0.9
            MODE = "reading"
            last_reading_success = None
            eye_display.set_mode("reading")
            dashboard_state.set_runtime(mode=MODE)
        elif MODE == "reading":
            # 持续读书模式，重新启动书本跟踪
            if is_reading_continue:
                eye_display.set_mode("speaking")
                if last_reading_success is False:
                    reading_retry_filler()
                else:
                    reading_next_page_filler()
            eye_display.set_mode("reading")
            _start_reading_tracking()
        elif is_story:
            conv.messages[0] = {
                "role": "system",
                "content": (
                    "你是一个擅长讲童话故事的机器人。请讲一个完整的儿童童话，有开头、发展、高潮、结尾。至少50字，最多100字，一口气讲完，不要中途停顿或问问题。"
                    "故事面向3-6岁儿童，情节要温暖、安全、简单、有画面感。"
                    "不要出现恐吓、血腥、危险模仿、责备孩子或过度悲伤的内容。"
                    "每句话尽量短一点，像睡前故事一样温柔。"
                    "\n\n多角色音色标注（重要）：每段对话开头用[音色名]标注说话者，旁白默认Cherry。可用音色和适合角色："
                    "\n[Cherry]亲切姐姐-旁白/叙述 [Ethan]阳光男声-年轻男性/爸爸 [Serena]温柔姐姐-妈妈/女性角色"
                    "\n[Stella]甜美少女-小女孩/公主 [Moon]帅气男声-少年/王子"
                    "\n[EldricSage]沉稳老者-爷爷/智者 [Pip]调皮小孩-小男孩"
                    "\n示例：\n[Cherry]从前有一座美丽的城堡\n[Stella]妈妈，我想出去玩\n[Serena]去吧，但要早点回来哦"
                    "输出纯文本，禁止使用任何Markdown格式符号（如**加粗**、#标题、*斜体）、emoji表情或装饰性字符。"
                ),
            }
            conv.max_tokens = 600
            conv.tools = None
            speaker.speed = 0.8
            MODE = "story"
            eye_display.set_mode("speaking")
            dashboard_state.set_runtime(mode=MODE)

        console_print("小智: ", end="", flush=True)
        response_text = conv.ask(text, cancel_event=cancel_event)

        # ── 打断处理 ──
        if cancel_event.is_set():
            reason = interrupt_reason
            interrupt_reason = None
            cancel_event.clear()
            if reason == "hard_stop":
                if MODE == "reading":
                    conv.max_tokens = saved_max_tokens
                    conv.tools = saved_tools
                    speaker.speed = saved_speed
                    conv.messages[0] = {"role": "system", "content": saved_system}
                    last_reading_success = None
                    MODE = "normal"
                    eye_display.set_mode("thinking")
                    dashboard_state.set_runtime(mode=MODE, is_processing=True)
                    _stop_reading_tracking(return_home=True)
                if MODE == "story":
                    conv.max_tokens = saved_max_tokens
                    conv.tools = saved_tools
                    speaker.speed = saved_speed
                    conv.messages[0] = {"role": "system", "content": saved_system}
                    MODE = "normal"
                    eye_display.set_mode("sleep")
                    dashboard_state.set_runtime(mode=MODE)
                is_processing = False
                auto_awake_requested = False
                asr.sleep()
                eye_display.set_mode("sleep")
                _sync_dashboard_runtime()
                print("[main] event=processing_interrupted reason=hard_stop asr=sleep", flush=True)
                return
            if MODE == "reading" and reason == "reading_pause":
                _stop_reading_tracking(return_home=False)
                is_processing = False
                auto_awake_requested = True
                asr.force_awake()
                eye_display.set_mode("listen")
                _sync_dashboard_runtime()
                print("[reading] event=paused reason=wake_interrupt mode=reading", flush=True)
                return
            if MODE == "reading":
                conv.max_tokens = saved_max_tokens
                conv.tools = saved_tools
                speaker.speed = saved_speed
                conv.messages[0] = {"role": "system", "content": saved_system}
                reading_out_filler()
                speaker.wait()
                eye_display.set_mode("thinking")
                _stop_reading_tracking(return_home=True)   # 打断退出读书模式，回初始位
                last_reading_success = None
                MODE = "normal"
                eye_display.set_mode("sleep")
                dashboard_state.set_runtime(mode=MODE)
            if MODE == "story":
                conv.max_tokens = saved_max_tokens
                conv.tools = saved_tools
                speaker.speed = saved_speed
                conv.messages[0] = {"role": "system", "content": saved_system}
                MODE = "normal"
                eye_display.set_mode("sleep")
                dashboard_state.set_runtime(mode=MODE)
            auto_awake_requested = False
            asr.sleep()
            eye_display.set_mode("sleep")
            _sync_dashboard_runtime()
            print("[main] event=processing_interrupted", flush=True)
            return

        dashboard_state.add_conversation("robot", response_text)
        if MODE == "reading":
            _stop_reading_tracking()  # 页间暂停：保持当前位姿等待下一页

        # ── 完成处理 ──
        if MODE == "reading":
            # 读书模式不下线，保持 prompt/tools 等待下轮
            reading_tool_result = getattr(conv, "last_reading_tool_result", {}) or {}
            reading_outcome = classify_reading_turn(response_text, reading_tool_result)
            reading_success = bool(reading_outcome["successful"])
            last_reading_success = reading_success
            dashboard_state.record_reading_result(response_text, successful=reading_success)
            print(
                "[reading] event=response_classified capture_ok={} model_success={} "
                "successful={} prompt_next_page={} retry={} asked_next_page={} "
                "raw_bytes={} b64_len={}".format(
                    reading_outcome["capture_ok"],
                    reading_outcome["model_success"],
                    reading_outcome["successful"],
                    reading_outcome["prompt_next_page"],
                    reading_outcome["retry"],
                    reading_outcome["asked_next_page"],
                    reading_tool_result.get("raw_bytes", 0),
                    reading_tool_result.get("b64_len", 0),
                ),
                flush=True,
            )
            if reading_outcome["prompt_next_page"]:
                reading_continue_filler()
                speaker.wait()
        if MODE == "story":
            conv.max_tokens = saved_max_tokens
            conv.tools = saved_tools
            speaker.speed = saved_speed
            conv.messages[0]["content"] = saved_system
            conv.messages.append({
                "role": "user",
                "content": "刚才讲的是一个虚构的童话故事，故事里的情节和人物不代表真实信息。请区分虚构与现实。后续继续用适合3-6岁小朋友的温柔方式回答。",
            })
            conv.messages.append({
                "role": "assistant",
                "content": "明白了。刚才那是故事创作，我会区分虚构故事和现实对话，后续继续用适合小朋友的方式回答。",
            })
            MODE = "normal"
            eye_display.set_mode("sleep")
            dashboard_state.set_runtime(mode=MODE)

        print("[main] event=processing_done mode={}".format(MODE), flush=True)
        is_processing = False
        auto_awake_requested = True
        if MODE == "reading":
            eye_display.set_mode("reading")
        else:
            eye_display.set_mode("listen")
        _sync_dashboard_runtime()

    def process_dashboard_speech():
        """Play one queued parent-dashboard message without blocking the mic pump."""
        item = dashboard_state.pop_speech_request()
        if not item:
            return
        try:
            text = item.get("text", "").strip()
            if not text:
                return
            print("[dashboard] event=speak source={} text={}".format(
                item.get("source", ""), text[:60]), flush=True)
            source = item.get("source", "")
            speaker.reset()
            speaker.feed(text)
            speaker.flush()
            if source not in {"parent", "sleep_remind"}:
                dashboard_state.add_conversation("robot", text, source=source)
        finally:
            dashboard_state.complete_speech_request()

    def process_person_task_events():
        """Record asynchronous person-task completion events from ROS monitors."""
        nonlocal active_person_task
        try:
            event = person_event_queue.get_nowait()
        except queue.Empty:
            return
        event_name = event.get("event")
        if event_name != "seek_arrived":
            return
        target_name = str(event.get("target_name") or event.get("target") or "")
        print("[person_task] event=seek_arrived target={} status={}".format(
            target_name, json.dumps(event.get("status", {}), ensure_ascii=False, sort_keys=True)),
            flush=True)
        voice_started = bool(active_person_task and active_person_task.get("action") == "seek")
        active_person_task = None
        dashboard_state.add_activity(
            "system",
            "人物任务: 已找到目标",
            kind="system",
            actor="robot",
            title="人物任务",
            meta=event,
        )
        dashboard_state.mark_person_task_done(reason="arrived", event=event)
        if voice_started:
            eye_display.set_mode("speaking")
            speaker.feed("我找到他了。")
            speaker.flush()
            eye_display.set_mode("sleep")

    def handle_interrupt():
        """主线程：处理打断"""
        nonlocal is_processing, interrupt_requested, idle_since, auto_awake_requested, MODE, interrupt_reason, pending_wake_reply

        # 1. 清空 TTS 播放队列 + 杀掉当前播放（不 reset，由 process_utterance 负责）
        speaker.cancel()

        # 2. 重置 AEC（清除残留的参考信号缓存）
        aec.reset()

        # 3. 清空 mic 管道残留（时限更短）
        drain_mic(mic.stdout, deadline_sec=1.0, label="打断")

        # 4. 清空 ASR 内部音频队列 (drain_mic 只清了管道)
        while not asr._audio_queue.empty():
            try:
                asr._audio_queue.get_nowait()
            except Exception:
                break

        # 5. 等待后台线程清理（状态切换由 process_utterance 负责）
        # asr.force_awake 已移至 process_utterance 打断分支
        if interrupt_reason == "hard_stop":
            asr.sleep()

        # 6. 重置所有共享状态
        is_processing = False
        interrupt_requested = False
        auto_awake_requested = False
        idle_since = time.time()
        _sync_dashboard_runtime()

        if pending_wake_reply:
            pending_wake_reply = False
            eye_display.set_mode("listen")
            _play_wake_reply_async()

        print("[interrupt] event=ready message=请说话", flush=True)

    # ── 主循环：永不阻塞的 mic 泵 ──
    loop_count = 0
    try:
        while True:
            loop_count += 1
            # 读 mic
            try:
                raw = mic.stdout.read(CHUNK_BYTES)
            except Exception:
                mic.terminate()
                time.sleep(0.1)
                mic = start_mic()
                continue

            if not raw:
                mic.terminate()
                time.sleep(0.3)
                mic = start_mic()
                continue

            # AEC 回声消除 → 投喂 ASR（SLEEP → KWS，AWAKE → VAD+ASR）
            clean_raw = aec.process_mic(raw)
            asr.feed(bytes(clean_raw))

            # 检查 ASR 识别结果
            try:
                r = result_queue.get_nowait()
            except queue.Empty:
                r = None

            if r and not is_processing:
                text = r.get("text", "").strip()
                if text in WAKE_WORDS:
                    idle_since = time.time()
                    print("[过滤] 控制词: '{}'".format(text), flush=True)
                    continue
                if _is_noise(text):
                    if text:
                        print("[过滤] 噪声: '{}'".format(text), flush=True)
                    continue
                # VAD 检测到静音 → 立即 SLEEP，后台处理
                idle_since = time.time()
                print("[识别] {}".format(text), flush=True)
                time.sleep(0.05)  # 等 _run 线程完成 VAD 段处理
                print("[main] event=recognized_sleep_request text={}".format(text), flush=True)
                asr.sleep()
                print("[main] event=asr_sleep_done awake={} qsize={}".format(
                    asr.is_awake, asr._audio_queue.qsize()), flush=True)
                is_processing = True
                _sync_dashboard_runtime()
                threading.Thread(target=process_utterance,
                                 args=(text,), daemon=True).start()

            # 处理中定期状态
            if is_processing and loop_count % 100 == 0:
                kws_chunks = getattr(asr, '_kws_chunks', 0)
                console_print("[处理中] loop={} is_awake={} KWS块={} qsize={}".format(
                    loop_count, asr.is_awake, kws_chunks,
                    asr._audio_queue.qsize()), flush=True, defer_during_stream=True)

            # 检查打断请求（由 on_wake 回调设置）
            if interrupt_requested:
                print("[main] event=interrupt_requested action=handle_interrupt", flush=True)
                handle_interrupt()

            # 家长端消息/睡眠提醒：主循环空闲时播报，不直接占用 HTTP 线程
            if not is_processing:
                process_person_task_events()
                process_dashboard_speech()

            # 检查自动唤醒（由 process_utterance 完成后设置）
            if auto_awake_requested and not is_processing:
                asr._request_state(True)
                auto_awake_requested = False
                idle_since = time.time()
                eye_display.set_mode("listen")
                _sync_dashboard_runtime()
                print("[main] event=auto_awake status=listening", flush=True)

            # 空闲超时 → 休眠（读书模式先退出）
            if (asr.is_awake and not is_processing and not asr.is_speaking()
                    and time.time() - idle_since > IDLE_SLEEP_SEC):
                if MODE == "reading":
                    conv.max_tokens = 200
                    conv.tools = TOOLS
                    speaker.speed = 1.0
                    conv.messages[0] = {"role": "system", "content": SYSTEM_PROMPT}
                    MODE = "normal"
                    eye_display.set_mode("thinking")
                    dashboard_state.set_runtime(mode=MODE)
                    cancel_event.clear()
                    interrupt_reason = None
                    reading_out_filler()
                    speaker.wait()
                    _stop_reading_tracking(return_home=True)   # 空闲退出读书模式，回初始位
                    cancel_event.clear()
                    interrupt_reason = None
                    eye_display.set_mode("sleep")
                    dashboard_state.set_runtime(mode=MODE)
                    print("[reading] event=exit reason=idle_timeout mode=normal", flush=True)
                asr.sleep()
                eye_display.set_mode("sleep")
                _sync_dashboard_runtime()
                idle_since = time.time()
                print("\n[main] event=idle_sleep seconds={}".format(IDLE_SLEEP_SEC))

    except KeyboardInterrupt:
        print("\n再见!")
    finally:
        sleep_presence_stop.set()
        _stop_person_tasks("shutdown_cleanup")
        person_task_controller.shutdown()
        if runtime_coordinator is not None:
            runtime_coordinator.shutdown()
        if dashboard_server is not None:
            dashboard_server.stop()
        if safety_guard is not None:
            safety_guard.stop()
        if chassis_control is not None and hasattr(chassis_control, "_ros_publisher"):
            try:
                chassis_control._ros_publisher.close()
            except Exception:
                pass
        if sleep_presence_thread is not None:
            sleep_presence_thread.join(timeout=1.0)
        asr.stop()
        try:
            mic.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    main()
