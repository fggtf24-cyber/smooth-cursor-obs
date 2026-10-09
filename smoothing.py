"""Сглаживание траектории курсора и выборка под кадры видео."""
import math

import numpy as np

STEP_MS = 1.0  # шаг симуляции


def deadzone(x, y, r):
    """Держит точку, пока сырой курсор не уйдёт дальше r — гасит дрожание руки."""
    ox, oy, ax, ay = [], [], x[0], y[0]
    for xi, yi in zip(x, y):
        if (xi - ax) ** 2 + (yi - ay) ** 2 > r * r:
            ax, ay = xi, yi
        ox.append(ax)
        oy.append(ay)
    return np.array(ox), np.array(oy)


def spring(tx, ty, reset, k, c, dt):
    """Пружина (масса 1): a = k·(цель − p) − c·v. Критическое демпфирование при c = 2·sqrt(k)."""
    ox, oy = np.empty(len(tx)), np.empty(len(tx))
    px, py, vx, vy = tx[0], ty[0], 0.0, 0.0
    for i, (gx, gy, r) in enumerate(zip(tx.tolist(), ty.tolist(), reset.tolist())):
        if r:
            px, py, vx, vy = gx, gy, 0.0, 0.0
        vx += (k * (gx - px) - c * vx) * dt
        vy += (k * (gy - py) - c * vy) * dt
        px += vx * dt
        py += vy * dt
        ox[i], oy[i] = px, py
    return ox, oy


def one_euro(tx, ty, reset, min_cutoff, beta, d_cutoff, dt):
    """One Euro filter (Casiez 2012); частота среза растёт со скоростью — меньше задержка на быстрых движениях."""
    alpha = lambda fc: 1 / (1 + 1 / (2 * math.pi * fc * dt))
    ad = alpha(d_cutoff)
    ox, oy = np.empty(len(tx)), np.empty(len(tx))
    px, py, dx, dy = tx[0], ty[0], 0.0, 0.0
    for i, (gx, gy, r) in enumerate(zip(tx.tolist(), ty.tolist(), reset.tolist())):
        if r:
            px, py, dx, dy = gx, gy, 0.0, 0.0
        dx += ad * ((gx - px) / dt - dx)
        dy += ad * ((gy - py) / dt - dy)
        a = alpha(min_cutoff + beta * math.hypot(dx, dy))
        px += a * (gx - px)
        py += a * (gy - py)
        ox[i], oy[i] = px, py
    return ox, oy


def click_weight(tg, click_times, pull_ms):
    """0..1 — насколько выход притянут к реальной позиции: 1 в момент нажатия/отпускания, плавно спадает за ±pull_ms."""
    w = np.zeros_like(tg)
    for t in click_times:
        lo, hi = np.searchsorted(tg, [t - pull_ms, t + pull_ms])
        u = np.clip(1 - np.abs(tg[lo:hi] - t) / pull_ms, 0, 1)
        w[lo:hi] = np.maximum(w[lo:hi], u * u * (3 - 2 * u))  # smoothstep
    return w


def frame_track(log, fps, n_frames, sm, offset_ms, ghost_ms=()):
    """Позиции курсора для кадров 0..n_frames-1 в пикселях дисплея.

    Кадр n показывает момент лога n/fps − offset_ms. ghost_ms — насколько раньше брать «призраков» для motion blur.
    """
    s = np.asarray(log["samples"], float)  # t_ms, x, y, type, showing
    t, x, y = s[:, 0], s[:, 1], s[:, 2]
    w, h = log["display"]["width"], log["display"]["height"]
    vis = (s[:, 4] > 0) & (x >= 0) & (x < w) & (y >= 0) & (y < h)

    tg = np.arange(t[0], t[-1] + STEP_MS, STEP_MS)
    j = np.clip(np.searchsorted(t, tg, "right") - 1, 0, len(t) - 1)  # последний сэмпл не позже момента сетки
    gvis = vis[j]
    reset = gvis & ~np.r_[False, gvis[:-1]]  # курсор появился — без «прилёта» из старой точки
    rx, ry = np.interp(tg, t, x), np.interp(tg, t, y)
    # нажатия: время и длительность (до отпускания той же кнопки; не отпущена — бесконечность)
    presses = []
    for k, (tc, btn, down, *_) in enumerate(log["clicks"]):
        if down == 1:
            up = next((c[0] for c in log["clicks"][k + 1:] if c[1] == btn and c[2] == 0), np.inf)
            presses.append((tc, up - tc))
    held = np.zeros(len(tg), bool)
    for tc, ln in presses:
        held |= (tg >= tc) & (tg <= tc + ln)
    dzx, dzy = deadzone(x, y, sm["deadzone_px"])
    tx, ty = np.interp(tg, t, dzx), np.interp(tg, t, dzy)
    # между видимым сэмплом и следующим невидимым (курсор ушёл с экрана) не смешиваем: иначе последняя видимая
    # точка тянется за край, а обратный проход фильтра разнёс бы это на сотни мс назад
    edge = vis[j] != vis[np.minimum(j + 1, len(t) - 1)]
    rx[edge], ry[edge], tx[edge], ty[edge] = x[j[edge]], y[j[edge]], dzx[j[edge]], dzy[j[edge]]
    # Сглаживаем без запаздывания: тот же фильтр вперёд и назад по времени (рендер знает будущее), запаздывания
    # взаимно гасятся. Иначе курсор на видео отстаёт от руки, а подсветка кнопок и пунктов списка — нет.
    # Пока кнопка зажата, фильтр стоит на реальной точке: иначе после отпускания курсор откатится к отставшему.
    ends = gvis & ~np.r_[gvis[1:], False]  # курсор пропадает — для обратного прохода это начало
    dt = STEP_MS / 1000
    if sm["method"] == "one_euro":
        e = sm["one_euro"]
        fx, fy = one_euro(tx, ty, reset | held, e["min_cutoff"], e["beta"], e["d_cutoff"], dt)
        bx, by = one_euro(fx[::-1], fy[::-1], (ends | held)[::-1], e["min_cutoff"], e["beta"], e["d_cutoff"], dt)
    else:
        k = 2 * sm["stiffness"]  # два прохода по 2× жёсткости плавны, как один прежний (тот же разгон за ступенькой)
        fx, fy = spring(tx, ty, reset | held, k, 2 * sm["damping_ratio"] * math.sqrt(k), dt)
        bx, by = spring(fx[::-1], fy[::-1], (ends | held)[::-1], k, 2 * math.sqrt(k), dt)  # назад — без перелёта
    sx, sy = bx[::-1].copy(), by[::-1].copy()
    pull = click_weight(tg, [c[0] for c in log["clicks"]], sm["click_pull_ms"])
    pull[held] = 1.0  # пока кнопка зажата (перетаскивание) — точно на реальной позиции, иначе отстаём от ползунка
    sx += (rx - sx) * pull
    sy += (ry - sy) * pull

    tf = np.arange(n_frames) * 1000 / fps - offset_ms
    idx = lambda tt: np.clip(np.round((tt - tg[0]) / STEP_MS).astype(int), 0, len(tg) - 1)
    i = idx(tf)
    downs = np.array([p[0] for p in presses] or [-np.inf])
    lens = np.array([p[1] for p in presses] or [0.0])
    last = np.maximum(np.searchsorted(downs, tf, "right") - 1, 0)
    click_age = tf - downs[last]  # мс с последнего нажатия
    press_len = lens[last]        # сколько кнопку держали (пока держат — inf)
    return {"t": tf, "x": sx[i], "y": sy[i], "raw_x": rx[i], "raw_y": ry[i], "visible": gvis[i],
            "type": s[j[i], 3].astype(int), "click_age": click_age, "press_len": press_len,
            "ghosts": [(sx[idx(tf - g)], sy[idx(tf - g)]) for g in ghost_ms],
            "path": (tg, sx, sy, gvis)}  # вся траектория с шагом 1 мс — для точного motion blur
