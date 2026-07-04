# eye_engine/__init__.py
"""机器人眼睛表情渲染引擎 — 子线程 Pygame 显示 + 共享状态控制"""
import threading
import time
import random

from eye_engine.eye_state import EyeState


def _run_eye_loop(eye_state: EyeState):
    """子线程：Pygame 渲染主循环，30fps"""
    from eye_engine import eye_renderer as _r

    _r.init_display()

    # 动画时间
    hp_t = 0.0   # happy
    sl_t = 0.0   # sleepy
    tt_t = 0.0   # thinking
    rd_t = 0.0   # reading
    nv_t = 0.0   # navigation

    # 自动眨眼
    ab_timer = 0.0
    ab_interval = random.uniform(3.0, 6.0)
    blinking = False
    bf, bt = 0, 0.0  # blink frame, blink timer

    current_expr = "neutral"
    current_trigger_time = 0.0
    running = True

    try:
        while running:
            dt = _r.clock.tick(30) / 1000.0

            # 读取共享状态
            expr, trigger_time = eye_state.snapshot()
            if expr != current_expr or trigger_time != current_trigger_time:
                current_expr = expr
                current_trigger_time = trigger_time
                # 重置动画时间
                hp_t = sl_t = tt_t = rd_t = nv_t = 0.0
                if current_expr == "thinking":
                    tt_t = getattr(_r, "THINKING_VISIBLE_START", 0.0)
                blinking = False
                ab_timer = 0.0
                ab_interval = random.uniform(3.0, 6.0)

            # 检查一次性眨眼
            if eye_state.consume_blink():
                blinking = True
                bf, bt = 0, 0.0

            # 自动眨眼（仅在 neutral 且非手动眨眼时）
            if current_expr == "neutral" and not blinking:
                ab_timer += dt
                if ab_timer >= ab_interval:
                    blinking = True
                    bf, bt = 0, 0.0
                    ab_timer = 0.0
                    ab_interval = random.uniform(3.0, 6.0)

            # 渲染当前表情
            if blinking:
                bt += dt
                if bt >= 0.12:
                    bt = 0.0
                    bf += 1
                if bf > 14:
                    blinking = False
                    bf = 0
                _r.draw_bl(bf)
                _r.render()

            elif current_expr == "neutral":
                _r.draw_n()
                _r.render()

            elif current_expr == "happy":
                hp_t += dt
                _r.draw_happy_frame(hp_t)
                _r.render()

            elif current_expr == "sleepy":
                sl_t += dt
                _r.draw_sleepy_frame(sl_t)
                _r.render()

            elif current_expr == "thinking":
                tt_t += dt
                _r.draw_think(tt_t)
                _r.render()

            elif current_expr == "reading":
                rd_t += dt
                _r.draw_reading(rd_t)
                dot_on = int(rd_t * 2.5) % 2 == 0
                _r.render(show_status=True, dot_on=dot_on)

            elif current_expr == "navigation":
                nv_t += dt
                _r.draw_nav(nv_t)
                _r.render()

            # 检查 pygame 退出事件
            for event in _r.pygame.event.get():
                if event.type == _r.pygame.QUIT:
                    running = False

    except Exception as e:
        with open("/tmp/eyes.log", "a") as f:
            f.write(f"EyeEngine error: {e}\n")
    finally:
        _r.pygame.quit()


def start(eye_state: EyeState):
    """启动眼睛渲染子线程（守护线程）"""
    t = threading.Thread(target=_run_eye_loop, args=(eye_state,),
                         daemon=True, name="eye_engine")
    t.start()
    return t
