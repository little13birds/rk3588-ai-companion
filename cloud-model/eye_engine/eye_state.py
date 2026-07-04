"""线程安全的表情状态 — 主线程写入，eye_engine 子线程读取"""
import threading
import time

VALID_EXPRESSIONS = {
    "neutral",     # 普通 — 胶囊眼 + 自动眨眼
    "happy",       # 开心 — ∩∩ 拱形笑眼 + 弹跳
    "sleepy",      # 困了 — 打瞌睡循环
    "thinking",    # 思考 — 放射加载圈旋转
    "reading",     # 读书 — 阅读眼镜 + 逐行
    "navigation",  # 导航 — 第一人称行驶（暂不使用）
}

class EyeState:
    def __init__(self):
        self._lock = threading.Lock()
        self.expression = "neutral"
        self.trigger_time = time.time()
        self.blink_now = False

    def set_expression(self, name: str):
        """切换表情，立即生效。无效名称被忽略。"""
        if name not in VALID_EXPRESSIONS:
            return
        with self._lock:
            self.expression = name
            self.trigger_time = time.time()

    def trigger_blink(self):
        """触发一次眨眼，不改变当前表情。"""
        with self._lock:
            self.blink_now = True

    def consume_blink(self) -> bool:
        """子线程调用：消费一次眨眼请求。"""
        with self._lock:
            if self.blink_now:
                self.blink_now = False
                return True
            return False

    def snapshot(self):
        """子线程调用：获取当前状态快照。"""
        with self._lock:
            return (self.expression, self.trigger_time)
