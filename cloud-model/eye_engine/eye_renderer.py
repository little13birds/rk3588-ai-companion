# eye_engine/eye_renderer.py
"""Pygame 子线程渲染器 — 从 robot_eyes.py 移植绘制逻辑"""
import os
os.environ['SDL_RENDER_SCALE_QUALITY'] = 'nearest'
# The eye GUI is visual-only.  Prevent pygame from opening the ALSA playback
# device, otherwise TTS aplay can fail with "Device or resource busy".
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

import pygame
import math

EYE_COLOR = (255, 248, 237)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

# 几何参数（运行时根据屏幕分辨率计算）
SW, SH = 0, 0
SCALE = 6
LW, LH = 0, 0
CX, CY = 0, 0
LX, LY = 0, 0
RX, RY = 0, 0
EW, EH = 19, 28
GAP = 57

# 思考参数
F_EH, F_EW, F_GS = 24, 19, 4
RAD_N = 8
RAD_CX, RAD_CY = 108, 22
RAD_IN, RAD_OUT = 2.0, 4.5
RAD_W = 1.3
RAD_STEP = 0.12
BR_PROFILE = [1.0, 0.55, 0.25, 0.10, 0.0, 0.0, 0.05, 0.15]
RAD_CYCLE = RAD_STEP * RAD_N

# 开心参数
H_ARCH_RX, H_ARCH_RY, H_ARCH_THICK = 11, 8, 3.5
H_LY, H_RY = 0, 0  # 运行时计算

# 读书参数
G_FW2, G_CR = 1.8, 6.5
G_LW, G_LH = 26, 24

# 导航参数
NV_LW = 1.2
NV_TOTAL = 12.0

# Pygame 对象（运行时初始化）
screen = None
logic = None
clock = None
_status_font = None
# ⚠️ Thread-affine: screen/logic/clock are created by init_display() on the
# rendering thread. All drawing functions using these globals MUST be called
# ONLY from that same thread. Do not call from the main thread.

# ⚠️ Thread-affine: RD_LX/RD_LY/RD_RX/RD_RY are written by draw_reading()
# on the rendering thread. Main thread reads are safe for display-only use
# (eventual consistency); do not rely on them for synchronization.


# ─── 缓动函数 ───

def eoc(t):
    return 1 - (1 - t) ** 3

def eio(t):
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2

def lerp(a, b, t):
    return a + (b - a) * t

def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ─── 基础绘制 ───

def capsule(ox, oy, h, w, alpha=1.0):
    """标准胶囊眼: 4层光晕 + 1层内核高光"""
    lyrs = [
        (w+6, h+6, EYE_COLOR, 15),
        (w+4, h+4, EYE_COLOR, 25),
        (w+2, h+2, EYE_COLOR, 46),
        (w, h, EYE_COLOR, 89),
    ]
    for lw, lh, clr, ba in lyrs:
        s = pygame.Surface((lw+4, lh+4), pygame.SRCALPHA)
        s.fill((0, 0, 0, 0))
        a = min(int(ba * alpha), 255)
        pygame.draw.rect(s, (*clr, a),
                         pygame.Rect(2, 2, lw, lh),
                         border_radius=lw//2)
        logic.blit(s, (ox - lw//2 - 2, oy - lh//2 - 2))
    cw, ch = w - 6, h - 6
    s2 = pygame.Surface((cw+4, ch+4), pygame.SRCALPHA)
    s2.fill((0, 0, 0, 0))
    ac = min(int(153 * alpha), 255)
    pygame.draw.rect(s2, (*WHITE, ac),
                     pygame.Rect(2, 2, cw, ch),
                     border_radius=cw//2)
    logic.blit(s2, (ox - cw//2 - 2, oy - ch//2 - 2))


def a_capsule(ox, oy, oh, ow, alpha=1.0):
    """自适应胶囊：圆角半径 = min(w,h)/2，自动适配竖/横形态"""
    lyrs = [
        (ow+6, oh+6, EYE_COLOR, 15),
        (ow+4, oh+4, EYE_COLOR, 25),
        (ow+2, oh+2, EYE_COLOR, 46),
        (ow, oh, EYE_COLOR, 89),
    ]
    for lw, lh, clr, ba in lyrs:
        s = pygame.Surface((lw+4, lh+4), pygame.SRCALPHA)
        s.fill((0, 0, 0, 0))
        a = min(int(ba * alpha), 255)
        gr = min(lw, lh) // 2
        if gr > 0:
            pygame.draw.rect(s, (*clr, a),
                             pygame.Rect(2, 2, lw, lh),
                             border_radius=gr)
        logic.blit(s, (ox - lw//2 - 2, oy - lh//2 - 2))
    cw, ch = ow - 6, oh - 6
    cr = min(cw, ch) // 2
    if cr > 0:
        s2 = pygame.Surface((cw+4, ch+4), pygame.SRCALPHA)
        s2.fill((0, 0, 0, 0))
        ac = min(int(153 * alpha), 255)
        pygame.draw.rect(s2, (*WHITE, ac),
                         pygame.Rect(2, 2, cw, ch),
                         border_radius=cr)
        logic.blit(s2, (ox - cw//2 - 2, oy - ch//2 - 2))


def draw_slit_eye(lx, ly, rx, ry, alpha):
    """半闭/闭合横线眼 — 用于眨眼和读书动画"""
    a = min(int(alpha * 89), 255)
    s = pygame.Surface((30, 4), pygame.SRCALPHA)
    s.fill((0, 0, 0, 0))
    pygame.draw.rect(s, (*EYE_COLOR, a), pygame.Rect(1, 1, 28, 2))
    logic.blit(s, (int(lx) - 14, int(ly)))
    logic.blit(s, (int(rx) - 14, int(ry)))

def clear():
    logic.fill(BLACK)


# ─── 拱形笑眼 ───

def draw_arch(cx, cy, rx, ry, thick, alpha=1.0):
    """绘制上半圆弧 ∩ 形笑眼"""
    if alpha < 0.02:
        return
    rect = pygame.Rect(cx - rx, cy - ry - thick//2, rx * 2, ry * 2 + thick)
    layers = [
        (thick+2.5, EYE_COLOR, int(alpha * 20)),
        (thick+1,   EYE_COLOR, int(alpha * 46)),
        (thick,     EYE_COLOR, int(alpha * 102)),
    ]
    for w, clr, a in layers:
        if a < 1:
            continue
        s = pygame.Surface((LW, LH), pygame.SRCALPHA)
        s.fill((0, 0, 0, 0))
        pygame.draw.arc(s, (*clr, min(a, 255)), rect, 0, math.pi, int(w))
        logic.blit(s, (0, 0))
    ac = min(int(alpha * 140), 255)
    if ac > 1:
        s2 = pygame.Surface((LW, LH), pygame.SRCALPHA)
        s2.fill((0, 0, 0, 0))
        pygame.draw.arc(s2, (*WHITE, ac), rect, 0, math.pi,
                        max(1, int(thick - 1.2)))
        logic.blit(s2, (0, 0))


def draw_morph_eye(cx, cy, h, w, arch_rx, arch_ry, thick, morph_t, alpha=1.0):
    """插值: 0=capsule, 1=arch"""
    if morph_t < 0.05:
        capsule(cx, cy, h, w, alpha)
    elif morph_t > 0.95:
        draw_arch(cx, cy, arch_rx, arch_ry, thick, alpha)
    else:
        capsule(cx, cy, h, w, alpha * (1 - morph_t))
        draw_arch(cx, cy, arch_rx, arch_ry, thick, alpha * morph_t)


# ─── 放射加载器 ───

def rad_seg(x1, y1, x2, y2, w, alpha):
    """单根放射线段，3层绘制"""
    if alpha < 0.02:
        return
    a = min(alpha, 1.5)
    g = pygame.Surface((LW, LH), pygame.SRCALPHA)
    g.fill((0, 0, 0, 0))
    ag = min(int(a * 30), 255)
    pygame.draw.line(g, (*EYE_COLOR, ag),
                     (int(x1), int(y1)), (int(x2), int(y2)), int(w + 1.5))
    logic.blit(g, (0, 0))
    b = pygame.Surface((LW, LH), pygame.SRCALPHA)
    b.fill((0, 0, 0, 0))
    ab = min(int(a * 115), 255)
    pygame.draw.line(b, (*EYE_COLOR, ab),
                     (int(x1), int(y1)), (int(x2), int(y2)), int(w))
    logic.blit(b, (0, 0))
    c = pygame.Surface((LW, LH), pygame.SRCALPHA)
    c.fill((0, 0, 0, 0))
    ac = min(int(a * 165), 255)
    pygame.draw.line(c, (*WHITE, ac),
                     (int(x1), int(y1)), (int(x2), int(y2)),
                     max(1, int(w - 0.6)))
    logic.blit(c, (0, 0))


def draw_loader(brightest, sub_t, flash=0.0):
    """放射加载图标: 8段亮度顺时针流动"""
    if flash > 0:
        for i in range(RAD_N):
            ang = i * math.pi * 2 / RAD_N - math.pi / 2
            x1 = RAD_CX + math.cos(ang) * RAD_IN
            y1 = RAD_CY + math.sin(ang) * RAD_IN
            x2 = RAD_CX + math.cos(ang) * RAD_OUT
            y2 = RAD_CY + math.sin(ang) * RAD_OUT
            rad_seg(x1, y1, x2, y2, RAD_W, flash)
        return
    for i in range(RAD_N):
        rel = (i - brightest) % RAD_N
        ca = BR_PROFILE[rel]
        nrel = (i - (brightest + 1)) % RAD_N
        na = BR_PROFILE[nrel]
        alpha = ca + (na - ca) * sub_t
        if alpha < 0.02:
            continue
        ang = i * math.pi * 2 / RAD_N - math.pi / 2
        x1 = RAD_CX + math.cos(ang) * RAD_IN
        y1 = RAD_CY + math.sin(ang) * RAD_IN
        x2 = RAD_CX + math.cos(ang) * RAD_OUT
        y2 = RAD_CY + math.sin(ang) * RAD_OUT
        rad_seg(x1, y1, x2, y2, RAD_W, alpha)


# ─── 表情渲染函数 ───

def draw_n():
    """普通表情: 静态双胶囊眼"""
    clear()
    capsule(LX, LY, EH, EW)
    capsule(RX, RY, EH, EW)


# ─── 开心动画 ───

HA_SURPRISE, HA_MORPH, HA_BOUNCE, HA_HOLD, HA_RETURN = 0.12, 0.25, 0.35, 1.3, 0.35
HP_S = HA_SURPRISE
HP_M = HP_S + HA_MORPH
HP_B = HP_M + HA_BOUNCE
HP_H = HP_B + HA_HOLD
HP_R = HP_H + HA_RETURN
HAPPY_TOTAL = HP_R


def draw_happy_frame(t):
    """开心动画: 放大 → ∩∩ 拱形 → 弹跳 → 浮动 → 恢复"""
    t = t % HAPPY_TOTAL
    lx, ly, lh, lw, la = float(LX), float(LY), float(EH), float(EW), 1.0
    rx, ry, rh, rw, ra = float(RX), float(RY), float(EH), float(EW), 1.0
    morph_t, offset_y = 0.0, 0.0

    if t < HP_S:
        st = t / HA_SURPRISE
        scale = 1 + 0.05 * eoc(st)
        lh *= scale; lw *= scale; rh *= scale; rw *= scale
    elif t < HP_M:
        mt = (t - HP_S) / HA_MORPH
        morph_t = eio(mt)
        m2 = min(1, mt * 2)
        lh = lerp(EH * 1.05, EH, m2); lw = lerp(EW * 1.05, EW, m2)
        rh = lerp(EH * 1.05, EH, m2); rw = lerp(EW * 1.05, EW, m2)
        ly = lerp(LY, H_LY, eio(mt)); ry = lerp(RY, H_RY, eio(mt))
    elif t < HP_B:
        morph_t = 1; ly, ry = H_LY, H_RY
        bt = (t - HP_M) / HA_BOUNCE
        if bt < 0.4:
            offset_y = -1.4 * math.sin(bt / 0.4 * math.pi / 2)
        else:
            settle = (bt - 0.4) / 0.6
            offset_y = -1.4 * math.exp(-settle * 5) * math.cos(settle * math.pi * 3)
    elif t < HP_H:
        morph_t = 1; ly, ry = H_LY, H_RY
        ht = (t - HP_B) / HA_HOLD
        offset_y = math.sin(ht * 1.5 * math.pi) * 0.6
    else:
        rt = (t - HP_H) / HA_RETURN
        morph_t = 1 - eio(rt)
        ly = lerp(H_LY, LY, eio(rt)); ry = lerp(H_RY, RY, eio(rt))
        lh = EH; lw = EW; rh = EH; rw = EW

    clear()
    ly += offset_y; ry += offset_y
    draw_morph_eye(int(lx), int(ly), int(lh), int(lw),
                   H_ARCH_RX, H_ARCH_RY, H_ARCH_THICK, morph_t, la)
    draw_morph_eye(int(rx), int(ry - 0.3), int(rh), int(rw),
                   H_ARCH_RX, H_ARCH_RY, H_ARCH_THICK, morph_t, ra)


# ─── 困了动画 ───

SL_IDLE, SL_D1, SL_HALF, SL_FIGHT, SL_FH, SL_D2, SL_NEAR, SL_REC = \
    0.5, 0.7, 1.0, 0.4, 0.3, 0.9, 1.5, 0.6
PSI = SL_IDLE
PSD1 = PSI + SL_D1
PSH = PSD1 + SL_HALF
PSF = PSH + SL_FIGHT
PSFH = PSF + SL_FH
PSD2 = PSFH + SL_D2
PSN = PSD2 + SL_NEAR
PSR = PSN + SL_REC
SL_TOT = PSR
S_HH, S_WH = 7, 22
S_HN, S_WN = 3, 25
S_HW, S_WW = 14, 20
S_DN = 1.5
S_AS = 0.7
S_DM = 0.7


def draw_sleepy_frame(t):
    """困了动画: 压扁 → 挣扎 → 再沉 → 近闭合 → 醒来"""
    t = t % SL_TOT
    lx, ly, lh, lw, la, ld = float(LX), float(LY), float(EH), float(EW), 1.0, 0.0
    rx, ry, rh, rw, ra, rd = float(RX), float(RY), float(EH), float(EW), 1.0, 0.0

    if t < PSI:
        pass
    elif t < PSD1:
        dt = (t - PSI) / SL_D1; e = eio(dt)
        lh = lerp(EH, S_HH, e); lw = lerp(EW, S_WH, e)
        rh = lerp(EH, S_HH, e); rw = lerp(EW, S_WH, e)
        ly = lerp(LY, LY + S_DN, e); ry = lerp(RY, RY + S_DN, e)
        la = lerp(1, S_DM, e); ra = lerp(1, S_DM, e)
    elif t < PSH:
        lh, rh = S_HH, S_HH; lw, rw = S_WH, S_WH
        ly, ry = LY + S_DN, RY + S_DN; la, ra = S_DM, S_DM
    elif t < PSF:
        ft = (t - PSH) / SL_FIGHT; e = eoc(ft)
        lh = lerp(S_HH, S_HW, e); lw = lerp(S_WH, S_WW, e)
        rh = lerp(S_HH, S_HW, e); rw = lerp(S_WH, S_WW, e)
        ly = lerp(LY + S_DN, LY, e); ry = lerp(RY + S_DN, RY, e)
        la = lerp(S_DM, 0.9, e); ra = lerp(S_DM, 0.9, e)
    elif t < PSFH:
        lh, rh = S_HW, S_HW; lw, rw = S_WW, S_WW
        ly, ry = LY, RY; la, ra = 0.9, 0.9
    elif t < PSD2:
        d2 = (t - PSFH) / SL_D2; e = eio(d2)
        lh = lerp(S_HW, S_HN, e); lw = lerp(S_WW, S_WN, e)
        rh = lerp(S_HW, S_HN, e); rw = lerp(S_WW, S_WN, e)
        ly = lerp(LY, LY + S_DN + 0.5, e); ry = lerp(RY, RY + S_DN + 0.5, e)
        la = lerp(0.9, S_DM * 0.85, e); ra = lerp(0.9, S_DM * 0.85, e)
        if d2 > 0.1:
            rd = (d2 - 0.1) / 0.9 * S_AS; ld = rd * 0.6
    elif t < PSN:
        lh, rh = S_HN, S_HN; lw, rw = S_WN, S_WN
        ly, ry = LY + S_DN + 0.5, RY + S_DN + 0.5 + S_AS
        la, ra = S_DM * 0.85, S_DM * 0.85; ld = S_AS * 0.6
    else:
        rt = (t - PSN) / SL_REC; e = eio(rt)
        lh = lerp(S_HN, EH, e); lw = lerp(S_WN, EW, e)
        rh = lerp(S_HN, EH, e); rw = lerp(S_WN, EW, e)
        ly = lerp(LY + S_DN + 0.5, LY, e)
        ry = lerp(RY + S_DN + 0.5 + S_AS, RY, e)
        la = lerp(S_DM * 0.85, 1, e); ra = lerp(S_DM * 0.85, 1, e)

    clear()
    a_capsule(int(lx), int(ly + ld), int(lh), int(lw), la)
    a_capsule(int(rx), int(ry + rd), int(rh), int(rw), ra)


# ─── 读书动画 ───

def draw_glasses(lx, ly, rx, ry, ga, gy):
    """阅读眼镜: 圆角方框镜片 + 弧形镜桥"""
    if ga < 0.02:
        return
    sa = ga * 0.7
    lcx, lcy = lx, ly + (gy or 0)
    rcx, rcy = rx, ry + (gy or 0)

    def lens(cx, cy, alpha):
        x, y = cx - G_LW / 2, cy - G_LH / 2
        s = pygame.Surface((LW, LH), pygame.SRCALPHA)
        s.fill((0, 0, 0, 0))
        a = min(int(alpha * 255), 255)
        pygame.draw.rect(s, (*EYE_COLOR, a),
                         pygame.Rect(x, y, G_LW, G_LH),
                         border_radius=int(G_CR), width=int(G_FW2))
        logic.blit(s, (0, 0))

    lens(lcx, lcy, sa * 0.2)
    lens(rcx, rcy, sa * 0.2)
    lens(lcx, lcy, sa)
    lens(rcx, rcy, sa)

    bx1, by1 = lcx + G_LW / 2, lcy
    bx2, by2 = rcx - G_LW / 2, rcy
    bmy = (by1 + by2) / 2 + 1.5
    bs = pygame.Surface((LW, LH), pygame.SRCALPHA)
    bs.fill((0, 0, 0, 0))
    a = min(int(sa * 255), 255)
    mx = (bx1 + bx2) / 2
    points = [(bx1, by1), (mx - 1, bmy - 0.5), (mx, bmy),
              (mx + 1, bmy - 0.5), (bx2, by2)]
    pygame.draw.lines(bs, (*EYE_COLOR, a), False,
                      [(int(p[0]), int(p[1])) for p in points], int(G_FW2))
    logic.blit(bs, (0, 0))


R5_D, R5_S, R5_L, R5_R, R5_B, R5_A = 0.5, 0.3, 1.8, 0.2, 0.7, 0.4
R5_LNS = 4
R5_PER = R5_D + R5_S + R5_L * R5_LNS + R5_R * (R5_LNS - 1) + R5_B + R5_A
RD_LX, RD_LY = 0.0, 0.0
RD_RX, RD_RY = 0.0, 0.0


def draw_reading(t):
    """读书动画: 戴眼镜 → 逐行阅读 → 翻页眨眼 → 调整眼镜"""
    global RD_LX, RD_LY, RD_RX, RD_RY
    t = t % R5_PER
    lx, ly, lh, lw, la = float(LX), float(LY), float(EH), float(EW), 1.0
    rx, ry, rh, rw, ra = float(RX), float(RY), float(EH), float(EW), 1.0
    ga, gy = 0.0, 0.0

    t0 = R5_D + R5_S; li = -1; lt = 0.0
    for i in range(R5_LNS):
        if t0 <= t < t0 + R5_L:
            li, lt = i, (t - t0) / R5_L; break
        t0 += R5_L
        if i < R5_LNS - 1 and t0 <= t < t0 + R5_R:
            li, lt = i + 0.5, (t - t0) / R5_R; break
        if i < R5_LNS - 1:
            t0 += R5_R
    blk = R5_D + R5_S + R5_L * R5_LNS + R5_R * (R5_LNS - 1)
    adj = blk + R5_B

    if t < R5_D:
        e = eoc(t / R5_D); ga = e; gy = lerp(-25, 0, e)
        ly = lerp(LY, LY + 2, e); ry = lerp(RY, RY + 2, e)
        lh = lerp(EH, 20, e); rh = lerp(EH, 20, e)
    elif t < R5_D + R5_S:
        ga = 1; gy = 0; ly = LY + 2; ry = RY + 2; lh = 20; rh = 20
    elif isinstance(li, int) and li >= 0 and li == int(li):
        by = LY + 2 + li * 0.6; ly = by; ry = by; lh = 20; rh = 20
        ga = 1; gy = 0
        p = eio(clamp(lt, 0, 1)); gaze = p * 4 - 2
        lx += gaze; rx += gaze
    elif li == 0.5 or li == 1.5 or li == 2.5:
        fl = int(li); ga = 1; gy = 0
        e = eio(clamp(lt, 0, 1))
        ly = lerp(LY + 2 + fl * 0.6, LY + 2 + (fl + 1) * 0.6, e)
        ry = lerp(RY + 2 + fl * 0.6, RY + 2 + (fl + 1) * 0.6, e)
        lh = 20; rh = 20
        gaze = lerp(2, -2, e); lx += gaze; rx += gaze
    elif t < adj:
        ga = 1; gy = 0
        ly = LY + 2 + R5_LNS * 0.6 - 0.6
        ry = RY + 2 + R5_LNS * 0.6 - 0.6
        lh = 20; rh = 20
        bt = (t - blk) / R5_B
        if bt < 0.2:
            a = lerp(1, 0.05, eoc(bt / 0.2)); la = a; ra = a
        elif bt < 0.55:
            la = 0.05; ra = 0.05
        else:
            a = lerp(0.05, 1, eio((bt - 0.55) / 0.45)); la = a; ra = a
    else:
        ga = 1; at = (t - adj) / R5_A
        if at < 0.4:
            gy = -1.5 * eoc(at / 0.4)
        else:
            gy = -1.5 * (1 - eio((at - 0.4) / 0.6))
        ly = LY + 2; ry = RY + 2; lh = 20; rh = 20

    RD_LX, RD_LY = lx, ly
    RD_RX, RD_RY = rx, ry
    clear()
    if la < 0.3 or ra < 0.3:
        draw_slit_eye(lx, ly, rx, ry, max(la, ra))
    else:
        capsule(int(lx), int(ly), int(lh), int(lw), clamp(la, 0, 2))
        capsule(int(rx), int(ry), int(rh), int(rw), clamp(ra, 0, 2))
    draw_glasses(lx, ly, rx, ry, ga, gy)


# ─── 导航动画 ───

def draw_road(curve):
    """道路透视线"""
    cx = LW // 2; ty, by = 43, 78
    tg, bg = 16, 90; vpx = cx + (curve or 0) * 8
    ltx, lbx = vpx - tg / 2, vpx - bg / 2
    rtx, rbx = vpx + tg / 2, vpx + bg / 2
    s = pygame.Surface((LW, LH), pygame.SRCALPHA); s.fill((0, 0, 0, 0))
    a = int(0.3 * 255)
    pygame.draw.line(s, (*EYE_COLOR, a), (int(ltx), ty), (int(lbx), by), int(NV_LW))
    pygame.draw.line(s, (*EYE_COLOR, a), (int(rtx), ty), (int(rbx), by), int(NV_LW))
    logic.blit(s, (0, 0))


def draw_markers(tm, curve):
    """地面标记: 5个小→大, 密→疏, 加速流向底部"""
    N = 5; vpx = LW // 2 + (curve or 0) * 8
    for i in range(N):
        ph = ((tm * 1.1 + i * 0.2) % 1.0 + 1.0) % 1.0
        p = ph * ph; y = lerp(50, 76, p)
        hg = lerp(8, 45, p)
        ox = (1 if i % 2 == 0 else -1) * lerp(0, 3, p)
        mx = vpx + (curve or 0) * lerp(0, 5, p) + ox
        mw, mh = lerp(3, 7, p), lerp(0.5, 1.2, p)
        alpha = p / 0.1 if p < 0.1 else (1 - p) / 0.15 if p > 0.85 else 1
        a = min(int(alpha * 0.35 * 255), 255)
        if a < 1:
            continue
        s = pygame.Surface((LW, LH), pygame.SRCALPHA); s.fill((0, 0, 0, 0))
        r = mh / 2; x = mx - mw / 2
        pygame.draw.rect(s, (*EYE_COLOR, a),
                         pygame.Rect(int(x), int(y), int(mw), int(mh)),
                         border_radius=1)
        logic.blit(s, (0, 0))


def draw_sidebars(tm):
    """侧面参照物: 左右竖条光块"""
    for bx, d in [(10, -1), (LW + 10, 1)]:
        for i in range(2):
            ph = ((tm * 0.7 + i * 0.35 + d * 0.1) % 1.0 + 1.0) % 1.0
            p = ph * ph; y = lerp(30, 75, p)
            x = bx + d * lerp(0, 5, p)
            alpha = ph / 0.1 if ph < 0.1 else (1 - ph) / 0.2 if ph > 0.8 else 1
            a = min(int(alpha * 0.2 * 255), 255)
            if a < 1:
                continue
            s = pygame.Surface((LW, LH), pygame.SRCALPHA); s.fill((0, 0, 0, 0))
            pygame.draw.rect(s, (*EYE_COLOR, a), pygame.Rect(int(x), int(y), 2, 3))
            logic.blit(s, (0, 0))


def draw_nav(tm):
    """导航动画: 第一人称行驶"""
    t = tm % NV_TOTAL
    curve = 0; gaze = 0
    if 3 < t < 4.5:
        curve = lerp(0, -1, eio(clamp((t - 3) / 0.8, 0, 1)))
        gaze = lerp(0, -3, clamp((t - 3) / 0.4, 0, 1))
    elif 4.5 <= t < 5.5:
        curve = -1; gaze = -3
    elif 5.5 <= t < 7:
        curve = lerp(-1, 0, eio(clamp((t - 5.5) / 0.8, 0, 1)))
        gaze = lerp(-3, 0, clamp((t - 5.5) / 0.5, 0, 1))
    elif 8 < t < 9.5:
        curve = lerp(0, 1, eio(clamp((t - 8) / 0.8, 0, 1)))
        gaze = lerp(0, 3, clamp((t - 8) / 0.4, 0, 1))
    elif 9.5 <= t < 10.5:
        curve = 1; gaze = 3
    elif t >= 10.5:
        curve = lerp(1, 0, eio(clamp((t - 10.5) / 0.8, 0, 1)))
        gaze = lerp(3, 0, clamp((t - 10.5) / 0.5, 0, 1))
    wobble = math.sin(tm * 3.5) * 1.2
    clear()
    capsule(int(LX + gaze), int(LY + wobble), int(EH * 0.92), EW, 1)
    capsule(int(RX + gaze), int(RY + wobble), int(EH * 0.92), EW, 1)
    draw_road(curve); draw_markers(tm, curve); draw_sidebars(tm)


# ─── 思考动画 ───

TB, TP, TF = 0.6, 0.25, 0.45; TLC = 4; TA, TR = 0.55, 0.5
PB = TB; PP = TB + TP; PF = TB + TP + TF
PL = PF + RAD_CYCLE * TLC; PA = PL + TA; PT = PA + TR; TT = PT
THINKING_VISIBLE_START = PF


def draw_think(t):
    """思考动画: 眨眼 → 专注 → 加载圈 → 答案闪亮 → 恢复"""
    t = t % TT
    lx, ly, lh, lw, la = float(LX), float(LY), float(EH), float(EW), 1.0
    rx, ry, rh, rw, ra = float(RX), float(RY), float(EH), float(EW), 1.0
    ls = False; bi, st, fl = 0, 0.0, 0.0

    if t < PB:
        bt = t / TB
        if bt < 0.25:
            a = lerp(1, 0.05, eoc(bt / 0.25)); la = a; ra = a
        elif bt < 0.55:
            la = 0.05; ra = 0.05
        else:
            a = lerp(0.05, 1, eio((bt - 0.55) / 0.45)); la = a; ra = a
    elif t < PP:
        pass
    elif t < PF:
        ft = (t - PP) / TF; e = eoc(ft)
        lh = lerp(EH, F_EH, e); lw = lerp(EW, F_EW, e); lx = LX + F_GS * e
        rh = lerp(EH, F_EH, e); rw = lerp(EW, F_EW, e); rx = RX - F_GS * e
    elif t < PL:
        ls = True; lt = t - PF
        lx, ly = LX + F_GS, float(LY); lh, lw = F_EH, F_EW
        rx, ry = RX - F_GS, float(RY); rh, rw = F_EH, F_EW
        ly += math.sin(lt * 1.7) * 0.8; ry += math.sin(lt * 1.7) * 0.8
        ts = lt / RAD_STEP; bi = int(ts) % RAD_N; st = ts - int(ts)
    elif t < PA:
        ls = True; at = (t - PL) / TA
        lx, ly = LX + F_GS, float(LY); lh, lw = F_EH, F_EW
        rx, ry = RX - F_GS, float(RY); rh, rw = F_EH, F_EW
        if at < 0.2:
            fl = at * 5; la = 1 + 0.3 * at; ra = 1 + 0.3 * at
        else:
            f = (at - 0.2) / 0.8; a = 1 - eoc(f); fl = a
            la = 1 + 0.3 * a; ra = 1 + 0.3 * a
    else:
        rt = (t - PA) / TR; e = eio(rt)
        lh = lerp(F_EH, EH, e); lw = lerp(F_EW, EW, e); lx = lerp(LX + F_GS, LX, e)
        rh = lerp(F_EH, EH, e); rw = lerp(F_EW, EW, e); rx = lerp(RX - F_GS, RX, e)

    clear()
    if la < 0.3 or ra < 0.3:
        draw_slit_eye(lx, ly, rx, ry, max(la, ra))
    else:
        capsule(int(lx), int(ly), int(lh), int(lw), clamp(la, 0, 2))
        capsule(int(rx), int(ry), int(rh), int(rw), clamp(ra, 0, 2))
    if ls:
        draw_loader(bi, st, fl)


# ─── 眨眼 ───

def draw_bl(f):
    """眨眼帧序列: 帧 0-6 正常, 7 半闭, 8 全闭, 9-12 保持, 13 半开, 14 恢复"""
    if f <= 6:
        draw_n()
    elif f in (7, 13):
        clear()
        for x, y in [(LX, LY), (RX, RY)]:
            s = pygame.Surface((28, 10), pygame.SRCALPHA); s.fill((0, 0, 0, 0))
            pygame.draw.rect(s, (*EYE_COLOR, 102), pygame.Rect(0, 0, 28, 10))
            logic.blit(s, (x - 14, y - 3))
    elif f == 8:
        clear()
        for x, y in [(LX, LY), (RX, RY)]:
            s = pygame.Surface((32, 3), pygame.SRCALPHA); s.fill((0, 0, 0, 0))
            pygame.draw.rect(s, (*EYE_COLOR, 89), pygame.Rect(0, 0, 32, 3))
            logic.blit(s, (x - 16, y))
    elif 9 <= f <= 12:
        pass
    else:
        draw_n()


# ─── 初始化 ───

def init_display():
    """初始化 Pygame 显示 — 在子线程中调用一次"""
    global screen, logic, clock, SW, SH, LW, LH, CX, CY, LX, LY, RX, RY
    global H_LY, H_RY, _status_font

    pygame.display.init()
    pygame.font.init()
    try:
        screen = pygame.display.set_mode((0, 0),
                                         pygame.FULLSCREEN | pygame.DOUBLEBUF)
    except Exception:
        screen = pygame.display.set_mode((1024, 600),
                                         pygame.FULLSCREEN | pygame.DOUBLEBUF)
    SW, SH = screen.get_width(), screen.get_height()
    SCALE = 6
    LW, LH = SW // SCALE, SH // SCALE
    pygame.mouse.set_visible(False)
    logic = pygame.Surface((LW, LH))
    clock = pygame.time.Clock()
    pygame.font.init()
    _status_font = None

    # 几何
    CX, CY = LW // 2, LH // 2 - 4
    LX, LY = CX - GAP // 2, CY
    RX, RY = CX + GAP // 2 + (GAP % 2), CY
    H_LY, H_RY = LY - 2, RY - 2

    with open('/tmp/eyes.log', 'w') as f:
        f.write(f"EyeEngine Screen:{SW}x{SH} Logic:{LW}x{LH} "
                f"Driver:{pygame.display.get_driver()}\n")


def render(show_status=False, dot_on=False):
    """缩放逻辑画布 → 物理屏幕 + flip"""
    global _status_font
    s = pygame.transform.scale(logic, (SW, SH))
    screen.blit(s, (0, 0))
    if show_status:
        if _status_font is None:
            _status_font = pygame.font.SysFont('sans-serif', 20)
        txt = _status_font.render('阅读中', True, (*EYE_COLOR, 90))
        screen.blit(txt, (SW * 340 // 720, SH * 430 // 480))
        if dot_on:
            dot = pygame.Surface((4, 4))
            dot.fill(EYE_COLOR)
            dot.set_alpha(140)
            screen.blit(dot, (SW * 440 // 720, SH * 436 // 480))
    pygame.display.flip()
