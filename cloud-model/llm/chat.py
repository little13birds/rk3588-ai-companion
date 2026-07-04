"""多轮对话引擎 — 滑动窗口 + 压缩摘要 + 图片摘要 + 持久化 + token统计 + 打断支持"""
import sys, os, time, base64, json, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openai import OpenAI
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from reading_mode import ReadingStreamFilter, is_reading_context
from utils.console_io import console_print, console_stream, console_write

client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

SUMMARY_MODEL = LLM_MODEL
SUMMARY_PREFIX = "提醒一下，根据我们之前的对话："

INTERRUPT_MARKER = " [此处对话被打断]"
READING_CAPTURE_DIR = os.environ.get("READING_CAPTURE_DIR", "reading_captures")


class Conversation:
    """多轮对话管理：滑动窗口 + 压缩摘要 + 图片摘要 + 持久化"""

    def __init__(self, system_prompt: str = "",
                 max_history: int = 20,
                 summary_interval: int = 3,
                 keep_recent: int = 2,
                 max_tokens: int = 200,
                 speaker=None,
                 tools: list = None,
                 book_match=None,
                 person_task_controller=None,
                 tool_executor=None,
                 on_stream_start=None):
        self.max_history = max_history
        self.summary_interval = summary_interval
        self.keep_recent = keep_recent
        self.max_tokens = max_tokens
        self.speaker = speaker
        self.tools = tools
        self.book_match = book_match
        self.person_task_controller = person_task_controller
        self.tool_executor = tool_executor
        self.on_stream_start = on_stream_start
        self.messages = []
        if system_prompt:
            self.messages.append({"role": "system", "content": system_prompt})
        self._system_prompt = system_prompt
        self.total_tokens_used = 0
        self.total_calls = 0
        self.summary_count = 0
        self.last_reading_tool_result = {}

    # ── 滑动窗口 ──────────────────────────────────
    def _trim(self):
        head = [m for m in self.messages if m["role"] == "system"]
        body = [m for m in self.messages if m["role"] != "system"]
        if len(body) > self.max_history:
            body = body[-self.max_history:]
        self.messages = head + body

    # ── 压缩摘要 ──────────────────────────────────
    def _maybe_summarize(self):
        body = [m for m in self.messages if m["role"] != "system"]
        user_count = sum(1 for m in body if m["role"] == "user")
        if user_count < self.keep_recent + self.summary_interval:
            return
        self._summarize(body)

    def _summarize(self, body: list):
        keep_count = self.keep_recent * 2
        old = body[:-keep_count]
        recent = body[-keep_count:]

        if not old:
            return

        old_text = self._messages_to_text(old)
        limit = max(40, min(int(len(old_text) * 0.15), 300))
        console_print("[压缩摘要中...] ", end="", flush=True)

        summary = client.chat.completions.create(
            model=SUMMARY_MODEL,
            messages=[
                {"role": "system", "content": (
                    "你是一个对话摘要助手。请按以下格式输出摘要：\n"
                    "第1句：用户姓名/身份（必须提取，如无则写未知）。\n"
                    "第2句：关键事实（偏好、任务、决定）。\n"
                    "第3句（如有）：图片内容简述。\n"
                    "总字数{}字以内。不要抒情，不要冗余。"
                ).format(limit)},
                {"role": "user", "content": "请总结以下对话：\n\n" + old_text},
            ],
            stream=False,
            timeout=30,
        )
        summary_text = summary.choices[0].message.content.strip()

        head = [m for m in self.messages if m["role"] == "system"]
        self.messages = head + [
            {"role": "user", "content": SUMMARY_PREFIX + summary_text},
            {"role": "assistant", "content": "明白了，已记住。"},
        ] + recent

        self.summary_count += 1
        console_print(" 完成 [{}条 → {}字]".format(len(old), len(summary_text)), flush=True)

    def _messages_to_text(self, msgs: list) -> str:
        """将消息列表转为可读文本，用于摘要请求。图片消息保留 [图片] 标记"""
        lines = []
        for m in msgs:
            role_map = {"user": "用户", "assistant": "助手", "tool": "工具"}
            role = role_map.get(m["role"], m["role"])
            content = m.get("content", "") or ""
            if isinstance(content, list):
                parts = []
                for p in content:
                    if not isinstance(p, dict):
                        continue
                    if p.get("type") == "image_url":
                        parts.append("[图片]")
                    elif p.get("type") == "text":
                        parts.append(p.get("text", ""))
                content = " ".join(parts)
            lines.append("[{}] {}".format(role, content))
        return "\n".join(lines)

    def _count_image_parts(self) -> int:
        count = 0
        for message in self.messages:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            count += sum(
                1
                for part in content
                if isinstance(part, dict) and part.get("type") == "image_url"
            )
        return count

    def _save_reading_capture(self, raw_jpg: bytes, img_b64: str,
                              book_detection: dict | None = None) -> str:
        """Persist the exact frame used for matching plus the VLM-sized image."""
        os.makedirs(READING_CAPTURE_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%S")
        capture_id = f"{stamp}_{int((time.time() % 1) * 1000):03d}"
        raw_path = os.path.join(READING_CAPTURE_DIR, f"{capture_id}_raw.jpg")
        vlm_path = os.path.join(READING_CAPTURE_DIR, f"{capture_id}_vlm.jpg")
        meta_path = os.path.join(READING_CAPTURE_DIR, f"{capture_id}.json")

        with open(raw_path, "wb") as f:
            f.write(raw_jpg or b"")
        vlm_bytes = base64.b64decode(img_b64) if img_b64 else b""
        with open(vlm_path, "wb") as f:
            f.write(vlm_bytes)

        meta = {
            "id": capture_id,
            "raw_path": raw_path,
            "vlm_path": vlm_path,
            "raw_bytes": len(raw_jpg or b""),
            "vlm_bytes": len(vlm_bytes),
            "history_image_parts_before_query": self._count_image_parts(),
        }
        if book_detection:
            meta["book_detection"] = book_detection
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        print(
            "[ReadingCapture] saved id={} raw={} vlm={} history_images={}".format(
                capture_id,
                raw_path,
                vlm_path,
                meta["history_image_parts_before_query"],
            ),
            flush=True,
        )
        return capture_id

    # ── API 调用 ──────────────────────────────────
    def _estimate_input_tokens(self):
        total_chars = 0
        for m in self.messages:
            content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
            if content is None:
                continue
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total_chars += len(part.get("text", ""))
        return total_chars // 2

    def _reading_system_prompt(self) -> str:
        if not self.messages or self.messages[0].get("role") != "system":
            return ""
        return self.messages[0].get("content", "") or ""

    def _prune_reading_history_for_fresh_photo(self):
        """Reading pages are independent: keep only system + current user turn."""
        if not is_reading_context(self._reading_system_prompt()):
            return

        head = [m for m in self.messages if m.get("role") == "system"]
        latest_user = None
        for message in reversed(self.messages):
            if message.get("role") == "user":
                latest_user = message
                break

        self.messages = head + ([latest_user] if latest_user else [])

    def _empty_api_result(self) -> dict:
        return {
            "text": "",
            "stats": {
                "ttft_ms": 0,
                "gen_s": 0,
                "total_s": 0,
                "input_tokens": self._estimate_input_tokens(),
                "output_tokens": 0,
                "chars": 0,
                "tokens_per_s": 0,
            },
        }

    def _call_api(self, stream: bool = True, cancel_event: threading.Event = None) -> dict:
        self._prune_reading_history_for_fresh_photo()
        self._trim()
        self._maybe_summarize()

        # ── tool calling 循环 ──
        if self.tools and is_reading_context(self._reading_system_prompt()):
            self.last_reading_tool_result = {}
            tool_started = time.time()
            result = self._execute_tool("take_photo", "{}", cancel_event=cancel_event)
            self.messages.append({
                "role": "user",
                "content": result,
            })
            console_print(
                "[tool] take_photo 读书快路径已执行 ({:.1f}s)，开始流式回答".format(
                    time.time() - tool_started,
                ),
                flush=True,
            )
            if cancel_event and cancel_event.is_set():
                console_print("[tool] take_photo cancelled before streaming answer", flush=True)
                return self._empty_api_result()
        elif self.tools:
            while True:
                input_est = self._estimate_input_tokens()
                t0 = time.time()
                console_print(
                    "[tool_call] request_start input_est={} tools={}".format(
                        input_est,
                        [t["function"]["name"] for t in self.tools],
                    ),
                    flush=True,
                )
                resp = client.chat.completions.create(
                    model=LLM_MODEL,
                    messages=self.messages,
                    tools=self.tools,
                    max_tokens=self.max_tokens,
                    stream=False,
                    timeout=30,
                )
                console_print(
                    "[tool_call] response_done elapsed={:.1f}s".format(
                        time.time() - t0,
                    ),
                    flush=True,
                )
                msg = resp.choices[0].message

                if msg.tool_calls:
                    self.messages.append({
                        "role": "assistant",
                        "content": msg.content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in msg.tool_calls
                        ],
                    })
                    tool_started = time.time()
                    for tc in msg.tool_calls:
                        result = self._execute_tool(
                            tc.function.name,
                            tc.function.arguments,
                            cancel_event=cancel_event,
                        )
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        })
                    elapsed = time.time() - tool_started
                    console_print(
                        "[tool] {} 已执行 ({:.1f}s)，开始流式回答".format(
                            ",".join(
                                tc.function.name
                                for tc in msg.tool_calls
                            ),
                            elapsed,
                        ),
                        flush=True,
                    )
                    if cancel_event and cancel_event.is_set():
                        console_print("[tool] cancelled before streaming answer", flush=True)
                        return self._empty_api_result()
                    break

                usage = resp.usage
                if usage:
                    self._add_usage(usage.prompt_tokens, usage.completion_tokens)
                break

        # ── 流式输出 ──
        input_tokens_est = self._estimate_input_tokens()
        t_start = time.time()
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=self.messages,
            stream=True,
            max_tokens=self.max_tokens,
            stream_options={"include_usage": True},
            timeout=30,
        )

        text = ""
        token_count = 0
        first_token_time = None
        stream_started = False
        input_tokens = input_tokens_est
        output_tokens = 0
        stream_filter = (
            ReadingStreamFilter()
            if is_reading_context(self._reading_system_prompt())
            else None
        )

        with console_stream():
            for chunk in response:
                if not chunk.choices:
                    if chunk.usage:
                        input_tokens = chunk.usage.prompt_tokens
                        output_tokens = chunk.usage.completion_tokens
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    if first_token_time is None:
                        first_token_time = time.time()
                    token_count += 1
                    emitted = (
                        stream_filter.feed(delta.content)
                        if stream_filter
                        else delta.content
                    )
                    if emitted:
                        text += emitted
                        console_write(emitted)
                        if not stream_started and self.on_stream_start:
                            stream_started = True
                            self.on_stream_start()
                        if self.speaker:
                            self.speaker.feed(emitted)
                if chunk.usage:
                    input_tokens = chunk.usage.prompt_tokens
                    output_tokens = chunk.usage.completion_tokens

            if stream_filter:
                emitted = stream_filter.flush()
                if emitted:
                    text += emitted
                    console_write(emitted)
                    if not stream_started and self.on_stream_start:
                        stream_started = True
                        self.on_stream_start()
                    if self.speaker:
                        self.speaker.feed(emitted)

        t_end = time.time()
        ttft = first_token_time - t_start if first_token_time else 0
        gen_time = t_end - first_token_time if first_token_time else 0

        console_print()
        return {
            "text": text,
            "stats": {
                "ttft_ms": ttft * 1000,
                "gen_s": gen_time,
                "total_s": t_end - t_start,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "chars": len(text),
                "tokens_per_s": token_count / gen_time if gen_time > 0 else 0,
            },
        }

    def _execute_tool(self, name: str, args_str: str, cancel_event: threading.Event = None):
        """执行工具调用，返回 content 列表"""
        import json as _json
        try:
            args = _json.loads(args_str)
        except Exception:
            args = {}

        if self.tool_executor is not None:
            return self.tool_executor(name, args_str)

        if name == "take_photo":
            from vision.camera import capture_raw_and_vlm
            is_reading = is_reading_context(self._reading_system_prompt())
            from audio.fillers import photo_filler, reading_photo_filler
            if is_reading:
                reading_photo_filler()
            else:
                photo_filler()
            print(
                "[tool:take_photo] begin is_reading={} wait_ready={} args={}".format(
                    is_reading, is_reading, args
                ),
                flush=True,
            )
            capture_started = time.time()
            raw_jpg, img_b64 = capture_raw_and_vlm(
                wait_ready=is_reading,
                cancel_event=cancel_event,
            )
            capture_elapsed = time.time() - capture_started
            if is_reading:
                self.last_reading_tool_result = {
                    "tool": "take_photo",
                    "capture_ok": bool(img_b64),
                    "raw_bytes": len(raw_jpg or b""),
                    "b64_len": len(img_b64 or ""),
                    "db_match": False,
                }
            print(
                "[tool:take_photo] capture_done ok={} elapsed={:.1f}s raw_bytes={} b64_len={}".format(
                    bool(img_b64),
                    capture_elapsed,
                    len(raw_jpg or b""),
                    len(img_b64 or ""),
                ),
                flush=True,
            )
            if img_b64:
                capture_id = None
                book_detection = None
                # ── Book matching (reading mode) ──────────
                if is_reading and self.book_match:
                    try:
                        detect_metadata = getattr(self.book_match, "detect_metadata", None)
                        if callable(detect_metadata):
                            book_detection = detect_metadata(raw_jpg)
                        capture_id = self._save_reading_capture(raw_jpg, img_b64, book_detection)
                        match_started = time.time()
                        matches = self.book_match.query(raw_jpg)
                        print(
                            "[BookMatch] query_done elapsed={:.1f}s count={} scores={}".format(
                                time.time() - match_started,
                                len(matches or []),
                                [
                                    round(m.get("score", 0.0), 3)
                                    for m in (matches or [])
                                    if m
                                ],
                            ),
                            flush=True,
                        )
                        threshold = float(os.environ.get("BOOK_MATCH_THRESHOLD", "0.3"))
                        valid = []
                        for idx, match in enumerate(matches or []):
                            if not match:
                                print(
                                    f"[BookMatch] decision[{idx}] reject reason=no_match",
                                    flush=True,
                                )
                                continue
                            score = float(match.get("score", 0.0))
                            text = match.get("text") or ""
                            book = match.get("book") or ""
                            page = match.get("page")
                            reasons = []
                            if score <= threshold:
                                reasons.append(f"score<={threshold:.2f}")
                            if not text:
                                reasons.append("empty_text")
                            if reasons:
                                print(
                                    "[BookMatch] decision[{}] reject book={} page={} score={:.3f} text_chars={} reason={}".format(
                                        idx,
                                        book,
                                        page,
                                        score,
                                        len(text),
                                        ",".join(reasons),
                                    ),
                                    flush=True,
                                )
                                continue
                            print(
                                "[BookMatch] decision[{}] accept book={} page={} score={:.3f} text_chars={}".format(
                                    idx,
                                    book,
                                    page,
                                    score,
                                    len(text),
                                ),
                                flush=True,
                            )
                            valid.append(match)
                        if valid:
                            if is_reading:
                                self.last_reading_tool_result["db_match"] = True
                                self.last_reading_tool_result["match_count"] = len(valid)
                            parts = [m["text"] for m in valid]
                            combined = "\n\n".join(parts)
                            info = ", ".join(
                                f"{m['book']} p{m['page']}" for m in valid)
                            print(
                                f"[BookMatch] matched: {info} chars={len(combined)}",
                                flush=True,
                            )
                            return [{"type": "text", "text": (
                                f"【逐字朗读以下内容，禁止增减任何文字。"
                                f"读完说\"需要继续读下一页吗？\"】\n\n"
                                f"{combined}"
                            )}]
                        print(
                            f"[BookMatch] no_valid_match threshold={threshold:.2f}",
                            flush=True,
                        )
                    except Exception as e:
                        print(f"[BookMatch] query error: {type(e).__name__}: {e}", flush=True)
                elif is_reading:
                    print("[BookMatch] unavailable book_match_client=None", flush=True)
                    capture_id = self._save_reading_capture(raw_jpg, img_b64)

                return [
                    {"type": "image_url",
                     "image_url": {"url": "data:image/jpeg;base64," + img_b64}},
                    {"type": "text", "text": (
                        "摄像头已拍摄照片。数据库未匹配到此页面，"
                        "请先判断照片中文字是否清晰可读。"
                        "如果没有文字、文字模糊、太小、反光、遮挡或无法确认原文，"
                        "不要猜测，不要硬读，直接告诉用户看不清，"
                        "请用户调整角度或距离后重新拍摄，并询问'需要再试一次吗？'。"
                        "只有当文字清晰可读时，才按照原文顺序逐字朗读。"
                        "不要输出自我介绍，不要说正在读书模式，"
                        "不要添加注释或解释。"
                        + (f" 调试图片编号：{capture_id}。" if capture_id else "")
                    ) if is_reading else (
                        "摄像头已拍摄照片，请基于上面这张照片回答用户。"
                    )},
                ]
            else:
                return [{"type": "text", "text": "摄像头拍照失败，请告知用户稍后重试。"}]

        if name == "get_temperature":
            from sensors.sensors import read_temperature
            try:
                d = read_temperature()
                return [{"type": "text", "text": f"当前温度 {d['temperature']:.1f}°C，湿度 {d['humidity']:.1f}%。"}]
            except Exception as e:
                return [{"type": "text", "text": f"温湿度传感器读取失败：{e}"}]

        if name == "get_brightness":
            from sensors.sensors import read_light
            try:
                lux = read_light()
                return [{"type": "text", "text": f"当前环境光照强度为 {lux:.1f} lux。"}]
            except Exception as e:
                return [{"type": "text", "text": f"光照传感器读取失败：{e}"}]

        if name == "get_motion":
            from sensors.sensors import read_motion
            try:
                m = read_motion()
                a = m["accel_g"]
                g = m["gyro_dps"]
                info = (
                    f"加速度(g): X={a['x']:.2f} Y={a['y']:.2f} Z={a['z']:.2f}。"
                    f"陀螺仪(°/s): X={g['x']:.1f} Y={g['y']:.1f} Z={g['z']:.1f}。"
                    f"Z轴加速度约{a['z']:.1f}g，"
                    + ("设备水平放置。" if abs(a['z']) > 0.8 else "设备处于倾斜或运动中。")
                )
                return [{"type": "text", "text": info}]
            except Exception as e:
                return [{"type": "text", "text": f"运动传感器读取失败：{e}"}]

        if name in {"control_person_follow", "observe_people_identity"}:
            from person_tasks.tools import execute_person_tool
            return execute_person_tool(
                name,
                args_str,
                controller=self.person_task_controller,
            )

        return [{"type": "text", "text": "未知工具: " + name}]

    def _add_usage(self, prompt: int, completion: int):
        self.total_tokens_used += prompt + completion
        self.total_calls += 1

    def _build_image_content(self, text: str, image_path: str):
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        return [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text", "text": text},
        ]

    # ── 摘要 token 估算 ───────────────────────────
    def _summary_tokens(self):
        chars = 0
        for m in self.messages:
            if isinstance(m["content"], str) and m["content"].startswith(SUMMARY_PREFIX):
                chars += len(m["content"])
        return chars // 2

    # ── 持久化 ────────────────────────────────────
    def _strip_images_for_save(self, msgs: list) -> list:
        """移除 base64 图片数据，替换为 [图片] 占位，减小文件体积"""
        result = []
        for m in msgs:
            content = m["content"]
            if isinstance(content, list):
                new_content = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        new_content.append({"type": "text", "text": "[图片]"})
                    else:
                        new_content.append(part)
                content = new_content
            result.append({"role": m["role"], "content": content})
        return result

    def save(self, path: str):
        """保存会话状态到 JSON 文件"""
        data = {
            "messages": self._strip_images_for_save(self.messages),
            "max_history": self.max_history,
            "summary_interval": self.summary_interval,
            "keep_recent": self.keep_recent,
            "total_tokens_used": self.total_tokens_used,
            "total_calls": self.total_calls,
            "summary_count": self.summary_count,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("[会话已保存到 {}]".format(path))

    @classmethod
    def load(cls, path: str, system_prompt: str = "") -> "Conversation":
        """从 JSON 文件恢复会话"""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        conv = cls(
            system_prompt="",
            max_history=data["max_history"],
            summary_interval=data["summary_interval"],
            keep_recent=data["keep_recent"],
        )
        conv.messages = data["messages"]
        conv.total_tokens_used = data["total_tokens_used"]
        conv.total_calls = data["total_calls"]
        conv.summary_count = data["summary_count"]

        if system_prompt:
            if conv.messages and conv.messages[0]["role"] == "system":
                conv.messages[0]["content"] = system_prompt
            else:
                conv.messages.insert(0, {"role": "system", "content": system_prompt})
            conv._system_prompt = system_prompt
        print("[会话已从 {} 恢复 ({}条消息, {} tokens)]"
              .format(path, conv.history_count, conv.total_tokens_used))
        return conv

    # ── 统计显示 ──────────────────────────────────
    def _print_stats(self, stats: dict):
        self.total_tokens_used += stats["input_tokens"] + stats["output_tokens"]
        self.total_calls += 1
        s = stats
        st = self._summary_tokens()
        line = "-" * 45
        print(line)
        print("  {:<16} {:>6.0f} ms".format("TTFT(首包延迟)", s["ttft_ms"]))
        print("  {:<16} {:>6.0f} ms".format("生成耗时", s["gen_s"] * 1000))
        print("  {:<16} {:>6.0f} ms".format("总耗时", s["total_s"] * 1000))
        print("  {:<16} {:>6}".format("输入 tokens", s["input_tokens"]))
        print("  {:<16} {:>6}".format("输出 tokens", s["output_tokens"]))
        print("  {:<16} {:>6}".format("输出字符数", s["chars"]))
        print("  {:<16} {:>6.1f} tok/s".format("生成速度", s["tokens_per_s"]))
        print("  {:<16} {:>6}".format("其中摘要 tokens", st))
        if self.speaker:
            ts = self.speaker.stats()
            print("  {:<16} {:>6.0f} ms ({}句)".format("TTS合成", ts["tts_ms"], ts["sentences"]))
        print("  {:<16} {:>6}".format("本次 tokens", s["input_tokens"] + s["output_tokens"]))
        print("  {:<16} {:>6} ({}次)".format("累计 tokens", self.total_tokens_used, self.total_calls))
        print(line)

    # ── 公开接口 ──────────────────────────────────
    def ask(self, text: str, cancel_event: threading.Event = None) -> str:
        """发起一轮对话。cancel_event 用于打断检测。"""
        self.messages.append({"role": "user", "content": text})
        result = self._call_api(cancel_event=cancel_event)

        if self.speaker:
            self.speaker.flush()
            self.speaker.wait()

        interrupted = cancel_event and cancel_event.is_set()

        content = result["text"]
        if interrupted:
            content += INTERRUPT_MARKER

        self.messages.append({"role": "assistant", "content": content})

        if not interrupted:
            self._print_stats(result["stats"])

        return result["text"]

    def ask_with_image(self, text: str, image_path: str,
                       cancel_event: threading.Event = None) -> str:
        """带图片的对话。cancel_event 用于打断检测。"""
        self.messages.append({
            "role": "user",
            "content": self._build_image_content(text, image_path),
        })
        result = self._call_api(cancel_event=cancel_event)

        if self.speaker:
            self.speaker.flush()
            self.speaker.wait()

        interrupted = cancel_event and cancel_event.is_set()

        content = result["text"]
        if interrupted:
            content += INTERRUPT_MARKER

        self.messages.append({"role": "assistant", "content": content})

        if not interrupted:
            self._print_stats(result["stats"])

        return result["text"]

    @property
    def history_count(self):
        return sum(1 for m in self.messages if m["role"] != "system")

    def show_stats(self):
        roles = {"system": 0, "user": 0, "assistant": 0}
        for m in self.messages:
            roles[m["role"]] += 1
        print("消息 — system:{} user:{} assistant:{}  max_history:{}  summary_interval:{}"
              .format(roles["system"], roles["user"], roles["assistant"],
                      self.max_history, self.summary_interval))
        print("已压缩 {} 次, 摘要 tokens: {}, 累计 {} 次调用, {} tokens"
              .format(self.summary_count, self._summary_tokens(),
                      self.total_calls, self.total_tokens_used))
