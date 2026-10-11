"""Smooth Cursor — окно программы. Запуск: двойной клик по app.pyw (или python app.pyw)."""
import collections
import copy
import ctypes as C
import ctypes.wintypes as W
import logging
import math
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

APP = "Smooth Cursor"
BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))  # рядом с exe — шрифты, иконка
try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont, ImageTk

    import win32cursor as wc  # первым из своих: включает DPI awareness
    import i18n
    import render
    import settings
    import updater
    from i18n import t
    from smooth_cursor import HotkeyThread, Recorder, parse_hotkey, rerender_paths
except Exception:
    tk.Tk().withdraw()
    messagebox.showerror(APP, "Не удалось запустить:\n\n" + traceback.format_exc()
                         + "\nУстановите зависимости: pip install -r requirements.txt")
    raise SystemExit(1)

log = logging.getLogger("smooth")

# Палитра: светло-серая бумага и почти чёрный, один акцент — синее свечение под курсором
BG = CARD = "#E6E6E6"
FIELD, PANEL, HOVER = "#EEEEEE", "#1C1C1C", "#D6D6D6"
LINE, RULE = "#BDBDBD", "#D0D0D0"
MUTE, INK2, INK, RED = "#8A8A8A", "#3A3A3A", "#1C1C1C", "#D0342C"
BLUE = (38, 50, 232)
F = {}  # шрифты, заполняются после создания окна


def caps(s):
    return s.upper()


def get(cfg, path):
    for k in path.split("."):
        cfg = cfg[int(k) if k.isdigit() else k]
    return cfg


def put(cfg, path, value):
    *head, last = path.split(".")
    for k in head:
        cfg = cfg[int(k) if k.isdigit() else k]
    cfg[int(last) if last.isdigit() else last] = value


def photo(im):
    return ImageTk.PhotoImage(im)


BUNDLED = {"Inter": "Inter-Regular.ttf", "Inter Medium": "Inter-Medium.ttf", "IBM Plex Mono": "IBMPlexMono-Regular.ttf"}


def load_fonts():
    """Шрифты из папки программы (Inter, IBM Plex Mono — свободная лицензия OFL) — только для этого процесса."""
    for f in (BASE / "fonts").glob("*.ttf"):
        C.windll.gdi32.AddFontResourceExW(str(f), 0x10, 0)  # FR_PRIVATE


def font_file(family):
    """Файл шрифта по имени семейства (для рисования текста через PIL): встроенный или установленный."""
    import winreg
    if family in BUNDLED and (BASE / "fonts" / BUNDLED[family]).exists():
        return str(BASE / "fonts" / BUNDLED[family])
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts") as k:
                i = 0
                while True:
                    name, value, _ = winreg.EnumValue(k, i)
                    i += 1
                    base = name.split(" (")[0]
                    if base in (family, family + " Regular", family + " Book"):
                        return value if os.path.isabs(value) else os.path.join(os.environ["WINDIR"], "Fonts", value)
        except OSError:
            continue
    return None


# ---------- виджеты: прямые углы, пиксели ----------
class Btn(tk.Canvas):
    """Кнопка-прямоугольник: primary — чёрная, outline — контур, seg — ячейка (выбранная — чёрная)."""

    def __init__(self, parent, text, command=None, kind="outline", small=False, big=False):
        self.f = F["btn_b"] if big else F["small"] if small else F["btn"]
        self.kind, self.command, self.enabled, self.selected, self.hover = kind, command, True, False, False
        self.padx, self.h = (28, 48) if big else (12, 26) if small else (16, 32)
        super().__init__(parent, height=self.h, bg=parent["bg"], highlightthickness=0, bd=0, cursor="hand2")
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<ButtonRelease-1>", lambda e: self.enabled and self.command and self.command())
        self.set_text(text)

    def _hover(self, on):
        self.hover = on
        self.draw()

    def set_text(self, text):
        self.text = text
        self.w = tkfont.Font(font=self.f).measure(text) + 2 * self.padx
        self.config(width=self.w)
        self.draw()

    def set_enabled(self, on):
        self.enabled = on
        self.config(cursor="hand2" if on else "arrow")
        self.draw()

    def draw(self):
        k = self.kind
        if not self.enabled:
            fill, outline, fg = (LINE, LINE, BG) if k == "primary" else (BG, LINE, LINE)
        elif k == "primary":
            fill = INK2 if self.hover else INK
            outline, fg = fill, BG
        elif self.selected:
            fill, outline, fg = INK, INK, BG
        else:
            fill, outline, fg = (HOVER if self.hover else BG), (INK if k == "outline" else LINE), INK
        self.delete("all")
        self.create_rectangle(0, 0, self.w - 1, self.h - 1, fill=fill, outline=outline)
        self.create_text(self.w / 2, self.h / 2, text=self.text, fill=fg, font=self.f)


class Toggle(tk.Canvas):
    """Пиксельный флажок: пустой квадрат / чёрный квадрат с окошком."""

    def __init__(self, parent, value, command):
        super().__init__(parent, width=16, height=16, bg=parent["bg"], highlightthickness=0, cursor="hand2")
        self.value, self.command = value, command
        self.bind("<Button-1>", lambda e: self.flip())
        self.draw()

    def flip(self):
        self.value = not self.value
        self.draw()
        self.command(self.value)

    def draw(self):
        self.delete("all")
        self.create_rectangle(0, 0, 15, 15, fill=INK if self.value else BG, outline=INK)
        if self.value:
            self.create_rectangle(5, 5, 10, 10, fill=BG, outline=BG)


class Slider(tk.Canvas):
    """Тонкая линия с квадратной ручкой. command(v) возвращает округлённое значение."""

    def __init__(self, parent, lo, hi, value, command, step):
        super().__init__(parent, height=22, bg=parent["bg"], highlightthickness=0, cursor="hand2", takefocus=1)
        self.lo, self.hi, self.v, self.command, self.step = lo, hi, value, command, step
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Button-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<MouseWheel>", lambda e: self.nudge(1 if e.delta > 0 else -1))
        self.bind("<Left>", lambda e: self.nudge(-1))
        self.bind("<Right>", lambda e: self.nudge(1))

    def _drag(self, e):
        self.focus_set()
        u = min(1, max(0, (e.x - 7) / max(1, self.winfo_width() - 14)))
        self.set(self.lo + (self.hi - self.lo) * u)

    def nudge(self, d):
        self.set(self.v + d * self.step)

    def set(self, v):
        self.v = self.command(min(self.hi, max(self.lo, v)))
        self.draw()

    def draw(self):
        w = self.winfo_width()
        x = 7 + (w - 14) * (self.v - self.lo) / (self.hi - self.lo)
        self.delete("all")
        self.create_line(7, 11, w - 7, 11, fill=LINE, width=1)
        self.create_line(7, 11, x, 11, fill=INK, width=2)
        self.create_rectangle(x - 6, 5, x + 6, 17, fill=INK, outline=INK)


class Gauge(tk.Canvas):
    """Прогресс из клеток."""

    def __init__(self, parent, cells=18):
        super().__init__(parent, width=cells * 13 - 2, height=11, bg=parent["bg"], highlightthickness=0)
        self.cells = cells
        self.set(0)

    def set(self, pct):
        self.delete("all")
        filled = round(self.cells * min(pct, 100) / 100)
        for i in range(self.cells):
            on = i < filled
            self.create_rectangle(i * 13, 0, i * 13 + 10, 10, fill=INK if on else BG, outline=INK if on else LINE)


def pixel_logo(parent):
    """Логотип — пиксельная стрелка с синим свечением (assets/icon.png), иначе — стрелка из клеток."""
    png = BASE / "assets" / "icon.png"
    if png.exists():
        img = photo(Image.open(png).resize((32, 32), Image.NEAREST))
        lab = tk.Label(parent, image=img, bg=parent["bg"], bd=0)
        lab.image = img
        return lab
    rows = ["X......", "XX.....", "XXX....", "XXXX...", "XXXXX..", "XXXXXX.", "XXXXXXX", "XXXX...", "XX.XX..",
            "X..XX..", "....XX.", "....XX."]
    c = tk.Canvas(parent, width=7 * 3, height=len(rows) * 3, bg=parent["bg"], highlightthickness=0)
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch == "X":
                c.create_rectangle(x * 3, y * 3, x * 3 + 2, y * 3 + 2, fill=INK, outline=INK)
    return c


class LivePreview(tk.Canvas):
    """Чёрный «рельеф» с пиксельными краями и цифрами, как в «Сапёре». Внутри курсор повторяет мышь со
    сглаживанием, анимацией клика и шлейфом — с текущими настройками. Рука оставляет затухающий
    пиксельный след, под курсором — синее пиксельное свечение."""
    CELL, FINE = 22, 11
    SHADES = ("#D9D9D9", "#B5B5B5", "#8F8F8F", "#6A6A6A", "#474747", "#2E2E2E")

    def __init__(self, parent, app, w, h):
        super().__init__(parent, width=w, height=h, bg=BG, highlightthickness=0)
        self.app = app
        c = self.CELL
        cols, rows = w // c, h // c
        ox, oy = (w - cols * c) // 2, (h - rows * c) // 2
        r0, r1, c1 = 2, rows - 3, cols - 4  # живая зона 16:9 справа, вокруг — «рельеф»
        c0 = c1 - round((r1 - r0 + 1) * 16 / 9) + 1
        black = self.terrain(rows, cols, (r0, c0, r1, c1))
        # рельеф рисуется один раз в картинку: сотни клеток и цифр на холсте тормозили бы перерисовку
        im = Image.new("RGB", (w, h), BG)
        d = ImageDraw.Draw(im)
        num_font = ImageFont.truetype(font_file(F["mono_s"][0]) or "consola.ttf", 11)
        for r in range(rows):
            for q in range(cols):
                x, y = ox + q * c, oy + r * c
                if black[r, q]:
                    d.rectangle([x, y, x + c - 1, y + c - 1], fill=PANEL)
                elif (n := int(black[max(0, r - 1):r + 2, max(0, q - 1):q + 2].sum())):
                    d.text((x + c / 2, y + c / 2), str(n), font=num_font, fill=MUTE, anchor="mm")
        self.terrain_img = photo(im)
        self.create_image(0, 0, anchor="nw", image=self.terrain_img)
        self.core = (ox + c0 * c, oy + r0 * c, ox + (c1 + 1) * c, oy + (r1 + 1) * c)  # всегда чёрное
        x0, y0, x1, _ = self.core
        self.create_text(x0 + 10, y0 + 8, anchor="nw", text=t("ЖИВОЕ ПРЕВЬЮ"), font=F["caps"], fill=BG)
        self.create_text(x0 + 10, y0 + 24, anchor="nw", text=t("двигайте мышью и кликайте"), font=F["small"],
                         fill=MUTE)
        self.hud = self.create_text(x1 - 10, y0 + 8, anchor="ne", text="", font=F["mono_s"], fill=MUTE)
        g = Image.new("RGBA", (9 * 8, 9 * 8), (0, 0, 0, 0))
        dr = ImageDraw.Draw(g)
        for i in range(9):
            for j in range(9):
                a = max(0.0, 1 - math.hypot(i - 4, j - 4) / 4.6)
                if a:
                    dr.rectangle([i * 8, j * 8, i * 8 + 7, j * 8 + 7], fill=(*BLUE, int(235 * a ** 1.3)))
        self.glow = photo(g)
        self.pix, self.trail = {}, collections.deque(maxlen=45)
        self.mon, self.last_t, self.press_t, self.release_t, self.was_down = None, time.perf_counter(), -1e9, -1e9, False
        self.click_anim_t = -1e9  # когда последний раз играла анимация клика: после неё наклон возвращается плавно
        self.tilt_deg = 0.0  # сглаженный угол наклона в движении
        self.handles = {wc.user32.LoadCursorW(None, cid): n for n, (cid, _) in wc.CURSORS.items()}
        self.sets, self.sig, self.sig_seen, self.sig_t = {}, None, None, 0
        self.pt, self.ci = W.POINT(), wc.CURSORINFO(cbSize=C.sizeof(wc.CURSORINFO))
        self.after(16, self.tick)

    @staticmethod
    def terrain(rows, cols, core):
        """Маска чёрного: ядро + органичная масса вокруг (сглаженный шум), «окна» у края, островки."""
        rng = np.random.default_rng(5)
        r0, c0, r1, c1 = core
        rr, cc = np.mgrid[0:rows, 0:cols]
        dist = np.hypot(np.maximum(c0 - cc, cc - c1).clip(0), np.maximum(r0 - rr, rr - r1).clip(0))
        n = rng.random((rows, cols))
        for _ in range(3):
            n = (n + np.roll(n, 1, 0) + np.roll(n, -1, 0) + np.roll(n, 1, 1) + np.roll(n, -1, 1)) / 5
        n = (n - n.mean()) / (n.std() + 1e-9)
        b = dist < 1.4 + 1.8 * n
        b &= ~((rng.random(b.shape) < 0.12) & (dist > 0) & (dist < 3))
        b |= (rng.random(b.shape) < 0.06) & (dist > 2.5) & (dist < 8)
        b[r0:r1 + 1, c0:c1 + 1] = True
        return b

    def cursor(self, name, level, tilt, ghost=None):
        """(картинка, hotspot): шаг анимации клика, шаг наклона в движении; ghost — номер «призрака» шлейфа.
        Спрайты собираются по мере надобности — их сотни, а нужны единицы."""
        rc = self.app.cfg["render"]
        sig = (rc["click_scale"], rc["click_tilt_deg"], rc["blur_opacity"], rc["blur_length"], rc["cursor_scale"],
               rc["motion_tilt_right_deg"], rc["motion_tilt_left_deg"])
        now = time.perf_counter()
        if sig != self.sig:  # пересобрать спрайты, когда ползунок замер на 0.15 с (не на каждом шаге)
            if sig != self.sig_seen:
                self.sig_seen, self.sig_t = sig, now
            if self.sig is None or now - self.sig_t > 0.15:
                self.sig, self.sets = sig, {}
        key = (name, level, tilt, ghost)
        if key not in self.sets:
            size = max(8, round(30 * rc["cursor_scale"]))
            if ghost is not None:
                _, hot, img = self.cursor(name, 0, tilt)
                a = render.ghost_alphas(rc)[ghost]
                img = img.copy()
                img.putalpha(img.getchannel("A").point(lambda v: round(v * a)))
            elif level or tilt:
                if (name, "big") not in self.sets:
                    self.sets[(name, "big")] = wc.cursor_sprite(name, size * 4)
                img, hot = render.pose_sprite(*self.sets[(name, "big")], rc, level, tilt)
            else:
                img, hot = wc.cursor_sprite(name, size)
            self.sets[key] = (photo(img), hot, img)
        return self.sets[key]

    def tick(self):
        if not self.winfo_exists():  # окно пересобрано (смена языка) — этот холст больше не нужен
            return
        self.after(16, self.tick)
        if not self.winfo_viewable():
            return
        u = wc.user32
        with wc.physical():  # окно масштабирует Windows, а курсор и мониторы — в настоящих пикселях
            u.GetCursorPos(C.byref(self.pt))
            u.GetCursorInfo(C.byref(self.ci))
        mon = next((m for m in self.app.monitors if m["left"] <= self.pt.x < m["left"] + m["width"]
                    and m["top"] <= self.pt.y < m["top"] + m["height"]), None)
        now = time.perf_counter()
        if mon is None:
            return
        x, y = self.pt.x - mon["left"], self.pt.y - mon["top"]
        sm, rc = self.app.cfg["smoothing"], self.app.cfg["render"]
        if mon is not self.mon:  # перешли на другой монитор — без «прилёта»
            self.mon, self.p, self.v, self.anchor, self.d = mon, [x, y], [0.0, 0.0], [x, y], [0.0, 0.0]
            self.pix.clear()
            self.trail.clear()
            self.itemconfig(self.hud, text=f"{mon['device'].split(chr(92))[-1]} · {mon['width']}×{mon['height']}")
        if (x - self.anchor[0]) ** 2 + (y - self.anchor[1]) ** 2 > sm["deadzone_px"] ** 2:
            self.anchor = [x, y]
        steps, dt = min(60, max(1, round((now - self.last_t) * 1000))), 0.001
        self.last_t = now
        for _ in range(steps):  # тот же фильтр, что при рендере, по шагам 1 мс
            for i in (0, 1):
                g = self.anchor[i]
                if sm["method"] == "one_euro":
                    e = sm["one_euro"]
                    alpha = lambda fc: 1 / (1 + 1 / (2 * math.pi * fc * dt))
                    self.d[i] += alpha(e["d_cutoff"]) * ((g - self.p[i]) / dt - self.d[i])
                    fc = e["min_cutoff"] + e["beta"] * math.hypot(*self.d)
                    self.p[i] += alpha(fc) * (g - self.p[i])
                else:
                    k = sm["stiffness"]
                    self.v[i] += (k * (g - self.p[i]) - 2 * sm["damping_ratio"] * math.sqrt(k) * self.v[i]) * dt
                    self.p[i] += self.v[i] * dt
        down = u.GetAsyncKeyState(1) < 0
        if down and not self.was_down:
            self.press_t = now
        elif self.was_down and not down:
            self.release_t = now
        self.was_down = down
        x0, y0, x1, y1 = self.core  # монитор вписан в живую зону без искажения пропорций
        s = min((x1 - x0) / mon["width"], (y1 - y0) / mon["height"])
        mx, my = x0 + ((x1 - x0) - mon["width"] * s) / 2, y0 + ((y1 - y0) - mon["height"] * s) / 2
        to = lambda px, py: (mx + px * s, my + py * s)
        rx, ry = to(x, y)
        self.pix[(int((rx - x0) // self.FINE), int((ry - y0) // self.FINE))] = now
        qx, qy = to(*self.p)
        self.trail.append((now, qx, qy))

        self.delete("dyn")
        for (i, j), born in list(self.pix.items()):  # пиксельный след руки, затухает за секунду
            age = now - born
            if age > 1.0:
                del self.pix[(i, j)]
                continue
            px, py = x0 + i * self.FINE, y0 + j * self.FINE
            col = self.SHADES[min(len(self.SHADES) - 1, int(age * len(self.SHADES)))]
            self.create_rectangle(px, py, px + self.FINE - 1, py + self.FINE - 1, fill=col, outline=col, tags="dyn")
        for i, (_, tx, ty) in enumerate(self.trail):
            if i % 3 == 0:
                self.create_rectangle(tx - 1, ty - 1, tx + 1, ty + 1, fill=MUTE, outline=MUTE, tags="dyn")
        if not self.ci.flags & 1:
            return
        self.create_image(qx + 8 - 36, qy + 10 - 36, anchor="nw", image=self.glow, tags="dyn")
        name = self.handles.get(self.ci.hCursor, "arrow")
        level = 0
        if rc["click_animation"]:
            held = np.inf if down else (self.release_t - self.press_t) * 1000  # пока ЛКМ зажата — наклон держится
            level = int(round(float(render.click_curve((now - self.press_t) * 1000, rc["click_ms"], held))
                              * (render.CLICK_STEPS - 1)))
        if level:
            self.click_anim_t = now
        (ta, xa), (tb, xb) = self.trail[max(0, len(self.trail) - 2)][:2], self.trail[-1][:2]
        target = float(render.tilt_angle((xb - xa) / max(tb - ta, 1e-3) / (mon["width"] * s),
                                         rc["motion_tilt_right_deg"], rc["motion_tilt_left_deg"],
                                         (now - self.click_anim_t) * 1000))
        # в реальном времени будущего не знаем — угол догоняет цель плавно (при рендере сглаживание без задержки)
        self.tilt_deg += (target - self.tilt_deg) * (1 - math.exp(-steps / render.TILT_SMOOTH_MS))
        tilt = 0 if level else round(self.tilt_deg / render.TILT_STEP_DEG)
        if rc["motion_blur"] and level == 0:
            alphas = render.ghost_alphas(rc)
            fps = self.app.last_fps or 30
            tts, txs, tys = zip(*self.trail)
            for gi in range(len(alphas)):
                # между точками следа (шаг ~16 мс) — линейно, иначе призраки слипаются в 1–2 копии курсора
                span = 1 / rc["shutter"] if self.app.accurate_blur() else rc["blur_length"] / fps
                tg = now - span * (gi + 1) / len(alphas)
                gx, gy = float(np.interp(tg, tts, txs)), float(np.interp(tg, tts, tys))
                if abs(gx - qx) + abs(gy - qy) > 0.5:
                    im, (hx, hy), _ = self.cursor(name, 0, render.ghost_tilt(tilt), ghost=len(alphas) - 1 - gi)
                    self.create_image(gx - hx, gy - hy, anchor="nw", image=im, tags="dyn")
        im, (hx, hy), _ = self.cursor(name, level, tilt)
        self.create_image(qx - hx, qy - hy, anchor="nw", image=im, tags="dyn")


class ClickStrip(tk.Canvas):
    """«Покой → нажатие → возврат» — курсор с текущими настройками анимации клика."""

    def __init__(self, parent, app):
        super().__init__(parent, height=112, bg=parent["bg"], highlightthickness=0)
        self.app, self.imgs = app, []
        self.bind("<Configure>", lambda e: self.draw())

    def draw(self):
        rc = self.app.cfg["render"]
        img, hot = wc.cursor_sprite("arrow", 84)
        big, big_hot = wc.cursor_sprite("arrow", 336)
        self.imgs, w = [], self.winfo_width()
        self.delete("all")
        for i, (p, title) in enumerate(((0, t("покой")), (1, t("нажатие")), (0.4, t("возврат")))):
            if p:
                im, h = render.press_sprite(big, big_hot, (1 - (1 - rc["click_scale"]) * p) / 4,
                                            rc["click_tilt_deg"] * p)
            else:
                im, h = img, hot
            self.imgs.append(photo(im))
            cx = w * (0.14 + 0.34 * i)
            self.create_rectangle(cx - 3, 18 - 3, cx + 3, 18 + 3, outline=MUTE)  # точка клика
            self.create_image(cx - h[0], 18 - h[1], anchor="nw", image=self.imgs[-1])
            self.create_text(cx + 14, 102, text=title, font=F["small"], fill=INK2)
            if i < 2:
                self.create_text(w * (0.31 + 0.34 * i), 50, text="→", font=F["body"], fill=INK2)


class MonitorPicker(tk.Canvas):
    """Схема мониторов как в «Параметры → Дисплей»; клик выбирает, выбранный — чёрный."""

    def __init__(self, parent, app, on_pick, current=None, height=120):
        super().__init__(parent, height=height, bg=parent["bg"], highlightthickness=0, cursor="hand2")
        self.app, self.on_pick, self.boxes, self.h = app, on_pick, [], height
        self.current = current or (lambda: (app.cfg["display"], app.detected))
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Button-1>", self.click)

    def draw(self):
        mons, w, h = self.app.monitors, self.winfo_width(), self.h
        x0, y0 = min(m["left"] for m in mons), min(m["top"] for m in mons)
        x1, y1 = max(m["left"] + m["width"] for m in mons), max(m["top"] + m["height"] for m in mons)
        s = min((w - 20) / (x1 - x0), (h - 10) / (y1 - y0))
        ox, oy = (w - (x1 - x0) * s) / 2, (h - (y1 - y0) * s) / 2
        sel, auto = self.current()
        self.delete("all")
        self.boxes = []
        for m in mons:
            name = m["device"].split("\\")[-1]
            a, b = ox + (m["left"] - x0) * s + 3, oy + (m["top"] - y0) * s + 3
            c, d = ox + (m["left"] + m["width"] - x0) * s - 3, oy + (m["top"] + m["height"] - y0) * s - 3
            on = sel == name or (sel == "auto" and auto == m["device"])
            self.create_rectangle(a, b, c, d, fill=INK if on else BG, outline=INK)
            fg = BG if on else INK
            self.create_text((a + c) / 2, (b + d) / 2 - 7, text=name.replace("DISPLAY", ""), font=F["mono_b"], fill=fg)
            self.create_text((a + c) / 2, (b + d) / 2 + 13, text=f"{m['width']}×{m['height']}", font=F["mono_s"],
                             fill=fg)
            if sel == "auto" and on:
                self.create_text(a + 6, b + 4, anchor="nw", text=t("АВТО"), font=F["caps"], fill=fg)
            self.boxes.append((a, b, c, d, name))

    def click(self, e):
        for a, b, c, d, name in self.boxes:
            if a <= e.x <= c and b <= e.y <= d:
                self.on_pick(name)


class UpdateCard(tk.Frame):
    """Карточка поверх окна: вышла новая версия → «Обновить» (скачать с прогрессом) или «Не сейчас»."""
    BAR = 24

    def __init__(self, app, info, on_update, on_skip):
        super().__init__(app.root, bg=BG, highlightthickness=1, highlightbackground=INK)
        self.app = app
        self.place(relx=0.5, rely=0.5, anchor="center")
        self.lift()
        p = tk.Frame(self, bg=BG)
        p.pack(padx=40, pady=(30, 28))
        top = tk.Frame(p, bg=BG)
        top.pack(fill="x")
        app.lbl(top, caps(t("Обновление")), "caps", INK).pack(side="left")
        app.lbl(top, f"{updater.VERSION} → {info['version']}", "mono", INK2).pack(side="right")
        head = tk.Canvas(p, width=520, height=112, bg=BG, highlightthickness=0)
        for i, line in enumerate(t("Вышла\nверсия {}.").format(info["version"]).split("\n")):
            head.create_text(-3, 14 + i * 48, anchor="nw", text=line, font=(F["head"][0], -46), fill=INK)
        head.pack(anchor="w")
        app.lbl(p, t("Программа закроется и через пару секунд откроется уже обновлённой. "
                     "Настройки и записи останутся на месте."), "para", INK2, justify="left",
                wraplength=520).pack(anchor="w", pady=(6, 18))
        row = tk.Frame(p, bg=BG)
        row.pack(fill="x", pady=(0, 18))
        self.bar = tk.Canvas(row, width=self.BAR * 13, height=10, bg=BG, highlightthickness=0)
        self.bar.pack(side="left")
        self.status = app.lbl(row, "", "mono_s", MUTE)
        self.status.pack(side="left", padx=(12, 0))
        self.set_bar(0)
        tk.Frame(p, height=1, bg=RULE).pack(fill="x", pady=(0, 16))
        bot = tk.Frame(p, bg=BG)
        bot.pack(fill="x")
        self.go = Btn(bot, t("Обновить"), on_update, kind="primary")
        self.go.pack(side="right")
        self.skip = Btn(bot, t("Не сейчас"), on_skip)
        self.skip.pack(side="right", padx=(0, 10))

    def set_bar(self, pct, text=""):
        self.bar.delete("all")
        for i in range(self.BAR):
            on = i < round(self.BAR * pct / 100)
            self.bar.create_rectangle(i * 13, 0, i * 13 + 9, 9, fill=INK if on else BG, outline=INK if on else LINE)
        if text:
            self.status.config(text=text, fg=MUTE)

    def busy(self):
        self.go.set_enabled(False)
        self.skip.set_enabled(False)
        self.go.set_text(t("Скачиваю…"))

    def failed(self, err):
        self.status.config(text=err, fg=RED)
        self.go.set_text(t("Ещё раз"))
        self.go.set_enabled(True)
        self.skip.set_enabled(True)


class Onboarding(tk.Frame):
    """Первая настройка поверх окна: язык → подключение OBS → экран и fps → сохранение → готово."""
    STEPS = 5

    def __init__(self, app, step=0):
        super().__init__(app.root, bg=BG)
        self.app, self.step = app, step
        self.mon_choice = next((m["device"].split("\\")[-1] for m in app.monitors if m["left"] == m["top"] == 0),
                               app.monitors[0]["device"].split("\\")[-1])
        self.fps = 60
        self.place(x=0, y=0, relwidth=1, relheight=1)
        self.lift()
        page = tk.Frame(self, bg=BG)
        page.pack(fill="both", expand=True, padx=app.padx, pady=app.pady)
        top = tk.Frame(page, bg=BG)
        top.pack(fill="x")
        pixel_logo(top).pack(side="left", padx=(0, 12))
        brand = tk.Frame(top, bg=BG)
        brand.pack(side="left")
        app.lbl(brand, "Smooth Cursor", "brand").pack(anchor="w")
        app.lbl(brand, t("первая настройка"), "brand", MUTE).pack(anchor="w")
        if app.cfg["ui"]["onboarded"]:  # уже настраивались — можно выйти, ничего не меняя
            Btn(top, t("Закрыть"), self.destroy, small=True).pack(side="right", anchor="n", padx=(30, 0))
        app.lang_switch(top).pack(side="right", anchor="n", padx=(30, 0))

        bot = tk.Frame(page, bg=BG)
        bot.pack(side="bottom", fill="x")
        tk.Frame(page, height=1, bg=RULE).pack(side="bottom", fill="x", pady=(0, 16))
        self.counter = app.lbl(bot, "", "mono", INK2)
        self.counter.pack(side="left")
        self.dots = tk.Canvas(bot, width=self.STEPS * 14, height=10, bg=BG, highlightthickness=0)
        self.dots.pack(side="left", padx=(16, 0))
        self.next_btn = Btn(bot, t("Далее"), self.next, kind="primary")
        self.next_btn.pack(side="right")
        self.back_btn = Btn(bot, t("Назад"), self.back)
        self.back_btn.pack(side="right", padx=(0, 10))

        body = tk.Frame(page, bg=BG)
        body.pack(fill="both", expand=True, pady=(34, 20))
        self.art = tk.Canvas(body, width=560, height=440, bg=BG, highlightthickness=0)
        self.art.pack(side="right", anchor="n")
        self.left = tk.Frame(body, bg=BG)
        self.left.pack(side="left", fill="both", expand=True, padx=(0, 60))
        self.render_step()

    # --- каркас шага ---
    def render_step(self):
        a = self.app
        for w in self.left.winfo_children():
            w.destroy()
        self.counter.config(text=f"{self.step + 1:02d} / {self.STEPS:02d}")
        self.dots.delete("all")
        for i in range(self.STEPS):
            on = i <= self.step
            self.dots.create_rectangle(i * 14, 0, i * 14 + 9, 9, fill=INK if on else BG, outline=INK if on else LINE)
        self.back_btn.set_enabled(self.step > 0)
        self.update_next()
        self.next_btn.set_text(t("Начать работу") if self.step == self.STEPS - 1 else t("Далее"))
        self.draw_art()
        title, builder = ((t("Привет.\nНастроим?"), self.s_hello), (t("Подключим\nOBS."), self.s_obs),
                          (t("Что\nзаписываем."), self.s_screen), (t("Куда и чем\nсохранять."), self.s_save),
                          (t("Готово."), self.s_done))[self.step]
        head = tk.Canvas(self.left, width=620, height=150 if "\n" in title else 80, bg=BG, highlightthickness=0)
        for i, line in enumerate(title.split("\n")):
            head.create_text(-4, i * 72, anchor="nw", text=line, font=F["head"], fill=INK)
        head.pack(anchor="w")
        builder(self.left)

    def draw_art(self):
        """Справа — «рельеф» из «Сапёра» с номером шага в чёрном."""
        w, h, c = 560, 440, 22
        cols, rows = w // c, h // c
        core = (5, 5, rows - 6, cols - 6)
        black = LivePreview.terrain(rows, cols, core)
        im = Image.new("RGB", (w, h), BG)
        d = ImageDraw.Draw(im)
        num_font = ImageFont.truetype(font_file(F["mono_s"][0]) or "consola.ttf", 11)
        for r in range(rows):
            for q in range(cols):
                x, y = q * c, r * c
                if black[r, q]:
                    d.rectangle([x, y, x + c - 1, y + c - 1], fill=PANEL)
                elif (n := int(black[max(0, r - 1):r + 2, max(0, q - 1):q + 2].sum())):
                    d.text((x + c / 2, y + c / 2), str(n), font=num_font, fill=MUTE, anchor="mm")
        self.art_img = photo(im)
        self.art.delete("all")
        self.art.create_image(0, 0, anchor="nw", image=self.art_img)
        self.art.create_text(core[1] * c + 18, core[0] * c + 6, anchor="nw", text=f"{self.step + 1:02d}",
                             font=(F["head"][0], -120), fill=BG)

    def para(self, parent, text, fg=INK2, pady=(0, 14)):
        lab = self.app.lbl(parent, text, "para", fg, justify="left", wraplength=600, anchor="w")
        lab.pack(anchor="w", pady=pady)
        return lab

    def status(self, parent):
        lab = self.app.lbl(parent, "", "body", INK, justify="left", wraplength=600, anchor="w")
        lab.pack(anchor="w", pady=(10, 0))
        return lab

    def update_next(self):
        """Дальше — только когда шаг пройден: OBS подключён и ffmpeg есть (сцену можно не трогать)."""
        a = self.app
        if self.winfo_exists():
            self.next_btn.set_enabled({1: bool(a.recorder), 3: a.encoder is not None}.get(self.step, True))

    def next(self):
        if self.step == self.STEPS - 1:
            return self.finish()
        self.step += 1
        self.render_step()

    def back(self):
        self.step = max(0, self.step - 1)
        self.render_step()

    def finish(self):
        self.app.cfg["ui"]["onboarded"] = True
        settings.save(self.app.cfg)
        self.destroy()

    # --- шаги ---
    def s_hello(self, p):
        self.para(p, t("Smooth Cursor записывает экран через OBS и делает курсор на видео плавным, как в Screen "
                       "Studio: сглаживает дрожание, показывает клики и шлейф движения. Настройка займёт минуту."))
        self.app.lbl(p, t("ЯЗЫК"), "caps", INK).pack(anchor="w", pady=(10, 6))
        row = tk.Frame(p, bg=BG)
        row.pack(anchor="w")
        for code, name in (("ru", "Русский"), ("en", "English")):
            b = Btn(row, name, lambda c=code: self.app.set_lang(c), kind="seg")
            b.selected = i18n.LANG == code
            b.draw()
            b.pack(side="left", padx=(0, 6))

    def s_obs(self, p):
        a = self.app
        self.para(p, t("1. Установите и откройте OBS Studio (версия 30 или новее).\n"
                       "2. В OBS: Сервис → Настройки сервера WebSocket.\n"
                       "3. Включите «Включить сервер WebSocket». Если включена аутентификация — нажмите «Показать "
                       "данные для подключения» и перенесите пароль сюда."))
        g = tk.Frame(p, bg=BG)
        g.pack(anchor="w", pady=(4, 0))
        for i, (text, path, kw) in enumerate((("Адрес", "obs.host", {"width": 18}),
                                              ("Порт", "obs.port", {"conv": int, "width": 8}),
                                              ("Пароль", "obs.password", {"show": "•", "width": 18}))):
            a.lbl(g, t(text)).grid(row=i, column=0, sticky="w", pady=3, padx=(0, 20))
            a.entry(g, path, **kw)[0].grid(row=i, column=1, sticky="w", ipady=3)
        row = tk.Frame(p, bg=BG)
        row.pack(anchor="w", pady=(14, 0))
        Btn(row, t("Проверить подключение"), self.check_obs, kind="primary").pack(side="left")
        self.obs_link = Btn(row, t("Скачать OBS"), lambda: os.startfile("https://obsproject.com/download"))
        self.obs_status = self.status(p)
        if a.recorder:
            self.obs_status.config(text="■ " + t("Подключено: OBS {}").format(a.spec.get("version", "")))

    def check_obs(self):
        self.obs_status.config(text=t("Подключаюсь…"), fg=INK2)
        self.app.connect()
        self.after(300, self.wait_obs)

    def wait_obs(self):
        if not self.winfo_exists() or self.step != 1:
            return
        if self.app.state == "busy":
            return self.after(200, self.wait_obs)
        if self.app.recorder:
            self.obs_status.config(text="■ " + t("Подключено: OBS {}").format(self.app.spec.get("version", "")),
                                   fg=INK)
            self.obs_link.pack_forget()
            self.update_next()
        else:
            self.obs_status.config(text=t("Не получилось: ") + getattr(self.app, "last_error", ""), fg=RED)
            self.obs_link.pack(side="left", padx=(10, 0))

    def s_screen(self, p):
        self.para(p, t("Выберите экран, который будете записывать, и частоту кадров. Кнопка ниже сама настроит "
                       "OBS: создаст сцену «Smooth Cursor» с захватом этого экрана без курсора, выставит разрешение "
                       "экрана и fps и сделает сцену текущей."))
        self.picker = MonitorPicker(p, self.app, self.pick, current=lambda: (self.mon_choice, None), height=130)
        self.picker.pack(fill="x", pady=(4, 10))
        row = tk.Frame(p, bg=BG)
        row.pack(anchor="w")
        self.app.lbl(row, t("Кадров в секунду")).pack(side="left", padx=(0, 16))
        self.fps_btns = {}
        for f in (30, 60):
            b = Btn(row, str(f), lambda f=f: self.set_fps(f), kind="seg", small=True)
            b.pack(side="left", padx=(0, 4))
            self.fps_btns[f] = b
        self.set_fps(self.fps)
        self.app.lbl(p, t("60 — плавнее, 30 — меньше файл и нагрузка"), "small", MUTE).pack(anchor="w", pady=(4, 0))
        Btn(p, t("Настроить OBS"), self.setup_obs, kind="primary").pack(anchor="w", pady=(16, 0))
        self.scr_status = self.status(p)

    def pick(self, name):
        self.mon_choice = name
        self.picker.draw()

    def set_fps(self, f):
        self.fps = f
        for k, b in self.fps_btns.items():
            b.selected = k == f
            b.draw()

    def setup_obs(self):
        a = self.app
        if not a.recorder:
            return self.scr_status.config(text=t("Сначала подключите OBS — шаг 2."), fg=RED)
        if a.recorder.recording:
            return self.scr_status.config(text=t("Идёт запись — сначала остановите её."), fg=RED)
        mon = next((m for m in wc.list_monitors() if m["device"].split("\\")[-1] == self.mon_choice), None)
        if mon is None:  # монитор отключили
            return self.scr_status.config(text=t("монитор «{}» не найден").format(self.mon_choice), fg=RED)
        self.scr_status.config(text=t("Настраиваю OBS…"), fg=INK2)

        def work():
            try:
                a.recorder.obs.setup_scene(mon, self.fps)
                a.cfg["display"] = "auto"
                a._check_display()
                a.post(self.scr_status.config, {"text": "■ " + t("Готово: сцена «Smooth Cursor», {} · {}×{} · {} fps")
                                                .format(self.mon_choice, mon["width"], mon["height"], self.fps),
                                                "fg": INK})
            except Exception as e:
                log.error("%s", e)
                a.post(self.scr_status.config, {"text": t("Не получилось: ") + str(e), "fg": RED})

        a.worker.submit(work)

    def s_save(self, p):
        a = self.app
        a.lbl(p, t("ПАПКА ЗАПИСЕЙ"), "caps", INK).pack(anchor="w", pady=(0, 6))
        row = tk.Frame(p, bg=BG)
        row.pack(anchor="w")
        self.folder_lbl = a.lbl(row, str(a.folder()), "mono_s", INK2)
        self.folder_lbl.pack(side="left", padx=(0, 12))
        Btn(row, t("Изменить…"), self.change_folder, small=True).pack(side="left")
        a.lbl(p, t("Сюда OBS сохраняет записи, а программа — готовые видео с плавным курсором."), "small",
              MUTE).pack(anchor="w", pady=(4, 18))
        a.lbl(p, t("FFMPEG И ВИДЕОКАРТА"), "caps", INK).pack(anchor="w", pady=(0, 6))
        self.enc_row = tk.Frame(p, bg=BG)
        self.enc_row.pack(anchor="w", fill="x")
        self.encoder_changed()
        a.lbl(p, t("ГОРЯЧАЯ КЛАВИША"), "caps", INK).pack(anchor="w", pady=(18, 6))
        row = tk.Frame(p, bg=BG)
        row.pack(anchor="w")
        e, var = a.entry(row, "hotkey", width=14, live=False)
        e.pack(side="left", ipady=3)
        e.bind("<Return>", lambda _: a.apply_hotkey(var.get()))
        Btn(row, t("Применить"), lambda: a.apply_hotkey(var.get()), small=True).pack(side="left", padx=10)
        a.lbl(row, t("старт и стоп записи из любой программы"), "small", MUTE).pack(side="left")

    def change_folder(self):
        self.app.choose_folder()
        self.folder_lbl.config(text=str(self.app.folder()))

    def encoder_changed(self):
        """Строка про ffmpeg: найден — какой кодировщик; нет — кнопка установки с прогрессом."""
        if self.step != 3 or not hasattr(self, "enc_row") or not self.enc_row.winfo_exists():
            return
        a = self.app
        self.update_next()
        for w in self.enc_row.winfo_children():
            w.destroy()
        if a.ffmpeg_installing:
            a.lbl(self.enc_row, t("Скачиваю ffmpeg…")).pack(side="left")
            self.gauge = Gauge(self.enc_row)
            self.gauge.pack(side="left", padx=12)
        elif a.encoder is None:
            a.lbl(self.enc_row, t("ffmpeg не найден — без него видео не собрать."), "body", RED).pack(anchor="w")
            Btn(self.enc_row, t("Установить ffmpeg (≈100 МБ)"), a.install_ffmpeg, kind="primary").pack(
                anchor="w", pady=(8, 0))
        else:
            a.lbl(self.enc_row, "■ " + t("ffmpeg есть, кодирует: ") + a.encoder_text()).pack(anchor="w")

    def s_done(self, p):
        self.para(p, t("Нажмите {} или «Начать запись», чтобы записать экран. Когда остановите, рядом с записью "
                       "появится видео с плавным курсором.").format(self.app.cfg["hotkey"]))
        self.para(p, t("Справа в окне — живое превью: двигайте мышью, чтобы увидеть, как будет выглядеть курсор. "
                       "Настройки сглаживания, клика и шлейфа — во вкладках. Эту настройку можно открыть снова "
                       "кнопкой «Настройка»."), MUTE)


# ---------- окно ----------
class App:
    def __init__(self, root):
        self.root, self.cfg = root, settings.load()
        self.q = queue.Queue()                    # (функция, аргументы) из рабочих потоков → поток окна
        self.worker = ThreadPoolExecutor(1)       # OBS: подключение, старт/стоп записи
        self.renderer = ThreadPoolExecutor(1)     # рендеры по очереди
        self.recorder = self.hotkey = None
        self.state, self.rec_t0, self.pending, self.cancel, self._save_job = "offline", 0, 0, False, None
        self.folder_pending = False  # папка выбрана без связи с OBS — применить при подключении
        self.monitors = wc.list_monitors()
        self.detected, self.last_fps, self.log_lines, self.log_win = None, None, [], None
        self.spec, self.page, self.encoder, self.onboarding = {"obs": "offline"}, "rec", None, None
        self.encoder_src = None  # для какого пути к ffmpeg определён кодировщик
        self.ffmpeg_installing = self.ffmpeg_asked = False
        i18n.LANG = self.cfg["ui"]["lang"]

        fams = set(tkfont.families())
        pick = lambda *names: next((n for n in names if n in fams), names[-1])
        sans = pick("Suisse Intl", "Inter", "Arial")
        sans_m = pick("Suisse Intl Medium", "Inter Medium", "Arial")
        mono = pick("SF Mono", "IBM Plex Mono", "Consolas")
        F.update(head=(sans, -74), body=(sans, 10), small=(sans, 9), para=(sans, 11), caps=(sans_m, 8),
                 nav=(sans, 9), brand=(sans_m, 10), btn=(sans_m, 10), btn_b=(sans_m, 11),
                 mono=(mono, 10), mono_s=(mono, 8), mono_b=(mono, 15))

        root.title(APP)
        # Вёрстка в пикселях; на маленьком экране (1366×768) — узкие поля и прокрутка вместо обрезанного низа
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        self.padx, self.pady = max(16, min(110, (sw - 1300) // 2)), (46, 34) if sh >= 1000 else (12, 12)
        root.geometry(f"{min(1460, sw - 40)}x{min(960, sh - 100)}")
        root.minsize(min(1400, sw - 60), min(900, sh - 120))
        if (BASE / "assets" / "icon.ico").exists():
            root.iconbitmap(default=str(BASE / "assets" / "icon.ico"))
        root.configure(bg=BG)
        root.option_add("*Font", F["body"])
        self.style()
        self.build()

        h = logging.Handler()
        h.emit = lambda r: self.post(self.append_log, h.format(r), r.levelno)
        h.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
        logging.getLogger("obsws_python").setLevel(logging.CRITICAL)
        root.report_callback_exception = lambda *e: log.error("".join(traceback.format_exception(*e)))
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(50, self.pump)
        self.apply_hotkey(initial=True)
        self.refresh_list()
        self.connect()
        self.renderer.submit(self._detect_encoder)
        if not self.cfg["ui"]["onboarded"]:
            self.open_onboarding()
        self.root.after(2000, self.watch_monitors)
        self.root.after(3000, lambda: threading.Thread(target=self._check_update, daemon=True).start())

    def watch_monitors(self):
        """Мониторы могли поменяться (подключили, сменили разрешение) — схема и живое превью берут свежие."""
        self.root.after(2000, self.watch_monitors)
        mons = wc.list_monitors()
        if mons and mons != self.monitors:  # пустой список бывает на миг при перенастройке экранов
            self.monitors = mons
            for p in (getattr(self, "picker", None), getattr(self.onboarding, "picker", None)):
                if p and p.winfo_exists():
                    p.draw()

    # ---------- обновления ----------
    def _check_update(self):
        try:
            info = updater.latest()
        except Exception as e:  # нет интернета, лимит GitHub API — молча, проверим при следующем запуске
            log.debug("update check: %s", e)
            return
        if info and info["version"] != self.cfg["ui"]["skip_version"]:
            self.post(self.offer_update, info)

    def offer_update(self, info):
        if self.state == "recording" or self.pending or (self.onboarding and self.onboarding.winfo_exists()):
            return self.root.after(60_000, self.offer_update, info)  # не мешаем записи и рендеру
        card = None

        def skip():
            self.cfg["ui"]["skip_version"] = info["version"]  # про эту версию больше не спрашиваем
            self.schedule_save()
            card.destroy()
            log.info(t("Обновление пропущено. Скачать можно здесь: %s"), updater.PAGE)

        def update():
            if not getattr(sys, "frozen", False):  # запуск из исходников — ставить нечего, открываем страницу
                card.destroy()
                return os.startfile(updater.PAGE)
            card.busy()
            log.info(t("Скачиваю обновление %s…"), info["version"])

            def work():
                try:
                    mb = info["size"] / 2 ** 20
                    path = updater.download(info, lambda p: self.post(
                        card.set_bar, p, f"{p * mb / 100:.1f} / {mb:.1f} MB"))
                    self.post(self.run_update, path)
                except Exception as e:
                    self.post(log.error, t("Не удалось обновиться: %s"), t(str(e)))
                    self.post(card.failed, t(str(e)))

            threading.Thread(target=work, daemon=True).start()

        card = UpdateCard(self, info, update, skip)

    def run_update(self, path):
        subprocess.Popen([str(path), "--update"])  # установщик дождётся закрытия программы и запустит новую
        self.on_close()

    def style(self):
        st = ttk.Style()
        st.theme_use("clam")
        st.configure("Treeview", background=BG, fieldbackground=BG, foreground=INK, borderwidth=0,
                     rowheight=30, font=F["body"])
        st.map("Treeview", background=[("selected", INK)], foreground=[("selected", BG)])
        st.configure("Treeview.Heading", background=BG, foreground=MUTE, font=F["caps"], relief="flat",
                     borderwidth=0, padding=(8, 6))
        st.map("Treeview.Heading", background=[("active", BG)])
        st.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        # полоса прокрутки: без стрелок, тонкая чёрная плашка на сером — как ползунки
        st.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        st.configure("Vertical.TScrollbar", troughcolor=BG, background=INK, bordercolor=BG, lightcolor=INK,
                     darkcolor=INK, gripcount=0, width=6, relief="flat", borderwidth=0)
        st.map("Vertical.TScrollbar", background=[("active", INK2), ("disabled", LINE)])

    # ---------- каркас ----------
    def post(self, fn, *args, **kw):
        self.q.put((fn, args, kw))

    def pump(self):
        self.root.after(50, self.pump)  # первым делом: на этой очереди хоткей и старт/стоп записи
        while True:
            try:
                fn, args, kw = self.q.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args, **kw)
            except tk.TclError:  # виджет успел исчезнуть (сменили шаг настройки, язык) — не страшно
                pass
            except Exception:
                if fn != self.append_log:
                    log.error(traceback.format_exc())
        if self.state == "recording":
            s = int(time.time() - self.rec_t0)
            self.time_lbl.config(text=f"{s // 60:02d}:{s % 60:02d}")
            self.rec_dot.config(fg=RED if s % 2 == 0 else BG)

    def schedule_save(self):
        if self._save_job:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(400, self._saved)

    def _saved(self):
        settings.save(self.cfg)
        self.mark_preset()
        if hasattr(self, "strip"):
            self.strip.draw()

    def lbl(self, parent, text="", font="body", fg=INK, **kw):
        return tk.Label(parent, text=text, font=F[font], fg=fg, bg=parent["bg"], **kw)

    def build(self):
        self.scroller = sc = tk.Canvas(self.root, bg=BG, highlightthickness=0)  # прокрутка, если окно ниже содержимого
        self.scrollbar = ttk.Scrollbar(self.root, command=sc.yview)
        sc.configure(yscrollcommand=self.scrollbar.set)
        sc.pack(side="left", fill="both", expand=True)
        self.holder = tk.Frame(sc, bg=BG)
        self.holder_win = sc.create_window(0, 0, anchor="nw", window=self.holder)
        sc.bind("<Configure>", lambda e: self.fit_page())
        self.root.bind_all("<MouseWheel>", self.wheel)
        page = tk.Frame(self.holder, bg=BG)  # «паспарту»: всё вписано в невидимую рамку с большими полями
        page.pack(fill="both", expand=True, padx=self.padx, pady=self.pady)

        # верх: знак слева, навигация справа — как у референса
        top = tk.Frame(page, bg=BG)
        top.pack(fill="x")
        pixel_logo(top).pack(side="left", padx=(0, 12))
        brand = tk.Frame(top, bg=BG)
        brand.pack(side="left")
        self.lbl(brand, "Smooth Cursor", "brand").pack(anchor="w")
        self.lbl(brand, t("для записей OBS"), "brand").pack(anchor="w")
        self.lang_switch(top).pack(side="right", anchor="n", padx=(36, 0))
        Btn(top, t("Настройка"), self.open_onboarding, kind="primary", small=True).pack(side="right", anchor="n",
                                                                                         padx=(8, 0))
        self.preset_btn = Btn(top, "", self.preset_menu, small=True)
        self.preset_btn.pack(side="right", anchor="n", padx=(30, 0))
        self.mark_preset()
        nav = tk.Frame(top, bg=BG)
        nav.pack(side="right", anchor="n")
        self.navs, tabs = {}, (("rec", t("Записи")), ("motion", t("Движение")), ("fx", t("Эффекты")),
                               ("out", t("Вывод")), ("obs", "OBS"))
        for name, title in tabs:
            item = tk.Frame(nav, bg=BG, cursor="hand2")
            item.pack(side="left", padx=(30, 0))
            label = self.lbl(item, caps(title), "nav", MUTE)
            label.pack()
            mark = tk.Frame(item, width=5, height=5, bg=BG)
            mark.pack(pady=(5, 0))
            for w in (item, label):
                w.bind("<Button-1>", lambda e, n=name: self.show(n))
            self.navs[name] = (label, mark)

        # главный блок: заголовок и запись слева, «рельеф» с живым превью справа
        hero = tk.Frame(page, bg=BG)
        hero.pack(fill="x", pady=(26, 0))
        left = tk.Frame(hero, bg=BG)
        left.pack(side="left", anchor="n", fill="y")
        right = tk.Frame(hero, bg=BG)
        right.pack(side="right", anchor="n")
        head = tk.Canvas(left, width=470, height=164, bg=BG, highlightthickness=0)
        head.create_text(-4, 0, anchor="nw", text="Smooth", font=F["head"], fill=INK)
        head.create_text(-4, 74, anchor="nw", text="cursor.", font=F["head"], fill=INK)
        head.pack(anchor="w")
        self.lbl(left, t("Записывайте как обычно — курсор на видео станет плавным сам. "
                         "Справа живое превью: так курсор будет выглядеть с текущими настройками."),
                 "para", INK2, justify="left", wraplength=430).pack(anchor="w", pady=(4, 22))
        row = tk.Frame(left, bg=BG)
        row.pack(anchor="w")
        self.rec_btn = Btn(row, t("Начать запись"), self.toggle, kind="primary", big=True)
        self.rec_btn.pack(side="left")
        self.key_lbl = self.lbl(row, self.cfg["hotkey"], "mono", MUTE)
        self.key_lbl.pack(side="left", padx=(16, 20))
        self.rec_dot = self.lbl(row, "■", "body", BG)
        self.rec_dot.pack(side="left")
        self.time_lbl = self.lbl(row, "00:00", "mono_b", LINE)
        self.time_lbl.pack(side="left", padx=(6, 0))
        self.toggle_row(left, t("Рендерить сразу после записи"), "ui.auto_render").pack(anchor="w", pady=(14, 0))
        now = tk.Frame(left, bg=BG)
        now.pack(anchor="w", pady=(22, 0))
        top_now = tk.Frame(now, bg=BG)
        top_now.pack(fill="x", pady=(0, 6))
        self.lbl(top_now, t("СЕЙЧАС В OBS"), "caps", INK).pack(side="left")
        Btn(top_now, t("Проверить"), self.refresh_obs, small=True).pack(side="left", padx=(14, 0))
        self.now = [self.lbl(now, "", "small", INK2, anchor="w") for _ in range(3)]
        for w in self.now:
            w.pack(anchor="w")

        self.preview = LivePreview(right, self, 726, 330)
        self.preview.pack(anchor="e")
        self.caption = tk.Frame(right, bg=BG, height=26, width=726)
        self.caption.pack(anchor="w", pady=(8, 0))
        self.caption.pack_propagate(False)
        self.legend = self.make_legend(self.caption)
        self.cap_text = self.lbl(self.caption, "", "small", INK2, anchor="w", justify="left")
        self.show_caption(None)

        # низ: рендер и журнал одной строкой
        bot = tk.Frame(page, bg=BG)
        bot.pack(side="bottom", fill="x")
        tk.Frame(page, height=1, bg=RULE).pack(side="bottom", fill="x", pady=(14, 14))
        self.lbl(bot, t("РЕНДЕР"), "caps", INK).pack(side="left")
        self.gauge = Gauge(bot)
        self.gauge.pack(side="left", padx=(14, 10))
        self.prog_pct = self.lbl(bot, "0%", "mono_s", INK2, width=4, anchor="w")
        self.prog_pct.pack(side="left")
        self.prog_text = self.lbl(bot, t("нет задач"), "small", MUTE, width=30, anchor="w")
        self.prog_text.pack(side="left", padx=(6, 0))
        Btn(bot, t("Отменить"), self.cancel_render, small=True).pack(side="left", padx=(4, 36))
        self.lbl(bot, t("ЖУРНАЛ"), "caps", INK).pack(side="left")
        Btn(bot, t("Весь журнал"), self.open_log, small=True).pack(side="right")
        self.last_log = self.lbl(bot, "", "mono_s", MUTE, anchor="w")
        self.last_log.pack(side="left", fill="x", expand=True, padx=(14, 12))

        # настройки — во всю ширину
        tk.Frame(page, height=1, bg=RULE).pack(fill="x", pady=(18, 18))
        body = tk.Frame(page, bg=BG)
        body.pack(fill="both", expand=True)
        self.pages = {}
        for name, builder in (("rec", self.tab_records), ("motion", self.tab_motion), ("fx", self.tab_effects),
                              ("out", self.tab_output), ("obs", self.tab_obs)):
            f = tk.Frame(body, bg=BG)
            builder(f)
            self.pages[name] = f
        self.show(self.page)
        self.set_state(self.state)
        self.set_spec()

    def fit_page(self):
        """Страница во всю ширину; по высоте — не меньше окна, а если не влезает — появляется прокрутка."""
        sc, need = self.scroller, self.holder.winfo_reqheight()
        w, h = sc.winfo_width(), max(sc.winfo_height(), need)
        sc.itemconfig(self.holder_win, width=w, height=h)
        sc.configure(scrollregion=(0, 0, w, h))
        if need > sc.winfo_height():
            self.scrollbar.pack(side="right", fill="y", before=sc)
        else:
            self.scrollbar.pack_forget()
            sc.yview_moveto(0)

    def wheel(self, e):
        """Колесо прокручивает страницу, если она не влезла, — кроме ползунков, списка записей и журнала."""
        w, ob = e.widget, self.onboarding
        if (self.scrollbar.winfo_ismapped() and isinstance(w, tk.Misc) and w.winfo_toplevel() is self.root
                and not isinstance(w, (Slider, tk.Text, ttk.Treeview)) and not (ob and ob.winfo_exists())):
            self.scroller.yview_scroll(-1 if e.delta > 0 else 1, "units")

    def lang_switch(self, parent):
        f = tk.Frame(parent, bg=parent["bg"])
        for code in ("ru", "en"):
            lab = self.lbl(f, code.upper(), "nav", INK if i18n.LANG == code else MUTE, cursor="hand2")
            lab.pack(side="left", padx=(0, 10))
            lab.bind("<Button-1>", lambda e, c=code: self.set_lang(c))
        return f

    def set_lang(self, code):
        if code == i18n.LANG:
            return
        i18n.LANG = self.cfg["ui"]["lang"] = code
        self.schedule_save()
        self.rebuild()

    def rebuild(self):
        """Пересобрать окно целиком (язык, пресет) — с тем же журналом, кодировщиком и шагом онбординга."""
        step =self.onboarding.step if self.onboarding and self.onboarding.winfo_exists() else None
        for w in self.root.winfo_children():
            w.destroy()
        self.log_win = None
        self.build()
        self.refresh_list()
        if self.log_lines:  # вернуть последнюю строку журнала
            text, tag = self.log_lines[-1]
            self.last_log.config(text=text.split("\n")[0][:110], fg=RED if tag == "err" else "#A15C00" if tag else MUTE)
        if self.encoder:
            self.show_encoder()
        if step is not None:
            self.open_onboarding(step)

    def open_onboarding(self, step=0):
        if self.onboarding and self.onboarding.winfo_exists():
            self.onboarding.destroy()
        self.onboarding = Onboarding(self, step)

    def _detect_encoder(self):
        self.encoder_src = self.cfg["render"]["ffmpeg"]
        try:
            ff = render.tool(self.cfg["render"]["ffmpeg"], "ffmpeg")
            self.encoder = render.pick_encoder(ff, "auto")
            log.info(t("Кодировщик: %s"), self.encoder)
        except Exception as e:
            self.encoder = None
            log.error("%s", e)
        self.post(self.show_encoder)

    def encoder_text(self):
        names = {"hevc_nvenc": t("видеокарта NVIDIA (HEVC)"), "hevc_amf": t("видеокарта AMD (HEVC)"),
                 "hevc_qsv": t("графика Intel (HEVC)"), "libx264": t("процессор (H.264) — медленнее")}
        return names.get(self.encoder, t("ffmpeg не найден"))

    def show_encoder(self):
        if hasattr(self, "enc_lbl") and self.enc_lbl.winfo_exists():
            self.enc_lbl.config(text=t("Авто сейчас: ") + self.encoder_text())
        onboarding = self.onboarding and self.onboarding.winfo_exists()
        if onboarding:
            self.onboarding.encoder_changed()
        elif self.encoder is None and not self.ffmpeg_installing and not self.ffmpeg_asked:
            self.ffmpeg_asked = True
            if messagebox.askyesno(APP, t("ffmpeg не найден — без него программа не соберёт видео. "
                                          "Скачать и установить его сейчас (≈100 МБ)?")):
                self.install_ffmpeg()

    def install_ffmpeg(self):
        """Скачать ffmpeg в фоне; прогресс — в строке «Рендер» и в онбординге."""
        if self.ffmpeg_installing:
            return
        self.ffmpeg_installing = True
        self.show_encoder()

        def work():
            try:
                path = render.install_ffmpeg(lambda pct: self.post(self.ffmpeg_progress, pct))
                self.cfg["render"]["ffmpeg"] = "auto"
                self.post(self.schedule_save)
                log.info(t("ffmpeg установлен: %s"), path)
            except Exception as e:
                log.error("%s", e)
            self.ffmpeg_installing = False
            self.post(self.set_progress, 0)
            self._detect_encoder()

        threading.Thread(target=work, daemon=True).start()

    def ffmpeg_progress(self, pct):
        self.set_progress(pct)
        self.prog_text.config(text=t("загрузка ffmpeg"), fg=INK)
        ob = self.onboarding
        if ob and ob.winfo_exists() and getattr(ob, "gauge", None) and ob.gauge.winfo_exists():
            ob.gauge.set(pct)

    def make_legend(self, parent):
        f = tk.Frame(parent, bg=BG)
        for draw, text in ((self._legend_raw, t("как двигалась рука")), (self._legend_cursor, t("курсор на видео")),
                           (self._legend_ghost, t("шлейф и анимация клика"))):
            c = tk.Canvas(f, width=34, height=16, bg=BG, highlightthickness=0)
            draw(c)
            c.pack(side="left")
            self.lbl(f, text, "small", INK2).pack(side="left", padx=(6, 22))
        return f

    @staticmethod
    def _legend_raw(c):
        for i, col in enumerate(("#2E2E2E", "#6A6A6A", "#B5B5B5")):
            c.create_rectangle(2 + i * 11, 3, 2 + i * 11 + 9, 12, fill=col, outline=col)

    @staticmethod
    def _legend_cursor(c):
        c.create_rectangle(4, 1, 18, 15, fill="#4650EA", outline="#4650EA")
        c.create_polygon(9, 3, 9, 15, 12, 12, 15, 16, 17, 15, 14, 11, 18, 11, fill=INK, outline=BG)

    @staticmethod
    def _legend_ghost(c):
        for x, col in ((4, LINE), (12, MUTE), (20, INK)):
            c.create_polygon(x, 2, x, 14, x + 3, 11, x + 6, 15, x + 8, 14, x + 5, 10, x + 9, 10, fill=col, outline="")

    def show_caption(self, text):
        if text:
            self.legend.pack_forget()
            self.cap_text.config(text=text)
            self.cap_text.pack(fill="both")
        else:
            self.cap_text.pack_forget()
            self.legend.pack(anchor="w")

    def show(self, name):
        self.page = name
        for n, f in self.pages.items():
            f.pack_forget()
            label, mark = self.navs[n]
            label.config(fg=INK if n == name else MUTE)
            mark.config(bg=INK if n == name else BG)
        self.pages[name].pack(fill="both", expand=True)
        self.root.after_idle(self.fit_page)  # вкладки разной высоты

    # ---------- элементы, привязанные к настройкам ----------
    def columns(self, p):
        l, r = tk.Frame(p, bg=BG), tk.Frame(p, bg=BG)
        l.grid(row=0, column=0, sticky="new")
        r.grid(row=0, column=1, sticky="new", padx=(70, 0))
        p.columnconfigure(0, weight=1, uniform="c")
        p.columnconfigure(1, weight=1, uniform="c")
        return l, r

    def section(self, parent, title):
        f = tk.Frame(parent, bg=BG)
        f.pack(fill="x", pady=(0, 16))
        h = tk.Frame(f, bg=BG)
        h.pack(fill="x", pady=(0, 6))
        self.lbl(h, caps(t(title)), "caps", INK).pack(side="left")
        body = tk.Frame(f, bg=BG)
        body.pack(fill="x")
        body.columnconfigure(0, minsize=150)
        body.columnconfigure(1, weight=1)
        return body, h

    def toggle_row(self, parent, text, path, on_change=None):
        f = tk.Frame(parent, bg=parent["bg"])

        def changed(v):
            put(self.cfg, path, v)
            self.schedule_save()
            if on_change:
                on_change()

        tg = Toggle(f, get(self.cfg, path), changed)
        tg.pack(side="left")
        lab = self.lbl(f, t(text), "body", INK, cursor="hand2")
        lab.pack(side="left", padx=(10, 0))
        lab.bind("<Button-1>", lambda e: tg.flip())
        return f

    def hint_on(self, widgets, title, text):
        """При наведении на строку — пояснение под превью."""
        for w in widgets:
            w.bind("<Enter>", lambda e: self.show_caption(
                f"{caps(t(title))}  —  {text() if callable(text) else t(text)}"), add="+")
            w.bind("<Leave>", lambda e: self.show_caption(None), add="+")

    def slider(self, parent, row, text, path, lo, hi, step, hint="", live_hint=None, fmt=None):
        """Строка: подпись | ползунок | значение. Пояснение — под превью при наведении."""
        name = self.lbl(parent, t(text))
        name.grid(row=row, column=0, sticky="w", pady=3)
        value = self.lbl(parent, "", "mono", INK, width=7, anchor="e")
        digits = max(0, -int(f"{step:e}".split("e")[1]))
        state = {"v": get(self.cfg, path)}

        def changed(v, save=True):
            v = round(round(v / step) * step, digits)
            v = int(v) if float(step).is_integer() else v
            state["v"] = v
            value.config(text=fmt(v) if fmt else f"{v:.{digits}f}")
            if save and v != get(self.cfg, path):
                put(self.cfg, path, v)
                self.schedule_save()
                if live_hint:
                    self.show_caption(f"{caps(t(text))}  —  {live_hint(v)}")
            return v

        s = Slider(parent, lo, hi, get(self.cfg, path), changed, step)
        s.grid(row=row, column=1, sticky="ew", padx=(0, 6))
        value.grid(row=row, column=2, sticky="e", padx=(4, 0))
        self.hint_on((name, s, value), text, (lambda: live_hint(state["v"])) if live_hint else hint)
        changed(get(self.cfg, path), save=False)

    def segmented(self, parent, path, options, on_change=None):
        f = tk.Frame(parent, bg=parent["bg"])
        btns = {}

        def pick(v, fire=True):
            for k, b in btns.items():
                b.selected = k == v
                b.draw()
            if fire:
                put(self.cfg, path, v)
                self.schedule_save()
                if on_change:
                    on_change(v)

        for v, label in options.items():
            b = Btn(f, t(label), lambda v=v: pick(v), kind="seg", small=True)
            b.pack(side="left", padx=(0, 4))
            btns[v] = b
        pick(get(self.cfg, path), fire=False)
        return f

    def entry(self, parent, path, width=24, show=None, conv=str, live=True):
        """Поле ввода; live — каждое изменение сразу в настройки (иначе значение забирает кнопка)."""
        var = tk.StringVar(value=f"{get(self.cfg, path):g}" if isinstance(get(self.cfg, path), float)
                           else get(self.cfg, path))

        def changed(*_):
            try:
                put(self.cfg, path, conv(var.get().replace(",", ".") if conv is float else var.get()))
                self.schedule_save()
            except ValueError:
                pass

        if live:
            var.trace_add("write", changed)
        e = tk.Entry(parent, textvariable=var, width=width, show=show, relief="flat", bg=FIELD, fg=INK,
                     insertbackground=INK, font=F["mono"], highlightthickness=1, highlightbackground=LINE,
                     highlightcolor=INK)
        return e, var

    # ---------- вкладки ----------
    def tab_records(self, p):
        top = tk.Frame(p, bg=BG)
        top.pack(fill="x", pady=(0, 8))
        self.lbl(top, t("ПАПКА ЗАПИСЕЙ"), "caps", INK).pack(side="left")
        self.folder_lbl = self.lbl(top, "", "mono_s", INK2, anchor="w")
        self.folder_lbl.pack(side="left", padx=(14, 10))
        Btn(top, t("Изменить…"), self.choose_folder, small=True).pack(side="left")
        Btn(top, t("Обновить список"), self.refresh_list, small=True).pack(side="right")

        b = tk.Frame(p, bg=BG)
        b.pack(side="bottom", fill="x", pady=(10, 0))
        Btn(b, t("Рендер"), self.render_selected, kind="primary").pack(side="left")
        Btn(b, "▶  " + t("Превью"), self.preview_selected).pack(side="left", padx=(8, 12))
        self.lbl(b, t("с")).pack(side="left")
        self.entry(b, "ui.preview_start", width=4, conv=float)[0].pack(side="left", padx=5, ipady=3)
        self.lbl(b, t("сек, длиной")).pack(side="left")
        self.entry(b, "ui.preview_len", width=3, conv=float)[0].pack(side="left", padx=5, ipady=3)
        self.lbl(b, t("сек")).pack(side="left")
        Btn(b, t("В папке"), self.show_selected, small=True).pack(side="right")
        Btn(b, t("Исходник"), lambda: self.open_selected(result=False), small=True).pack(side="right", padx=6)
        Btn(b, t("Открыть"), lambda: self.open_selected(result=True), small=True).pack(side="right")

        lf = tk.Frame(p, bg=BG, highlightthickness=1, highlightbackground=LINE)
        lf.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(lf, columns=("date", "result"), selectmode="extended", height=4)
        for col, title, w, anchor in (("#0", t("ЗАПИСЬ"), 520, "w"), ("date", t("ДАТА"), 200, "center"),
                                      ("result", t("ПЛАВНЫЙ КУРСОР"), 200, "center")):
            self.tree.heading(col, text=title, anchor=anchor)
            self.tree.column(col, width=w, anchor=anchor)
        sb = ttk.Scrollbar(lf, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=2)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda e: self.open_selected(result=True))

    PRESET_NAMES = {"standard": "Стандарт", "light": "Лёгкий", "cinema": "Кино", "tilt": "С наклоном",
                    "tilt_back": "Наклон v2", "clean": "Без эффектов"}

    def accurate_blur(self):
        rc = self.cfg["render"]
        return rc["motion_blur"] and rc["blur_accurate"]

    def mark_preset(self):
        if hasattr(self, "preset_btn") and self.preset_btn.winfo_exists():
            cur = settings.preset_of(self.cfg)
            acc = " · " + t("точный блюр") if self.accurate_blur() else ""
            self.preset_btn.set_text(f"{t('Пресет')}: {self.preset_label(cur) if cur else t('свой')}{acc}  ▾")

    def preset_label(self, key):
        return key[3:] if key.startswith("my:") else t(self.PRESET_NAMES[key])  # своё имя — как есть

    def preset_menu(self):
        """Выпадающий список пресетов под кнопкой — в стиле программы, закрывается кликом мимо или Esc.
        Ниже пресетов — точный motion blur: он с любым пресетом, поэтому отдельным флажком."""
        b, cur = self.preset_btn, settings.preset_of(self.cfg)
        m = tk.Toplevel(self.root, bg=INK)
        m.overrideredirect(True)
        m.geometry(f"+{b.winfo_rootx()}+{b.winfo_rooty() + b.winfo_height() + 4}")
        box = tk.Frame(m, bg=BG)
        box.pack(padx=1, pady=1)

        def item(marked, label, action, delete=None):
            row = tk.Frame(box, bg=BG, cursor="hand2")
            row.pack(fill="x")
            mark = self.lbl(row, "■" if marked else "", "small", INK, width=2)
            mark.pack(side="left", padx=(10, 0), pady=7)
            lab = self.lbl(row, label, "body", INK, anchor="w", width=20)
            lab.pack(side="left", padx=(2, 14))
            parts = [row, mark, lab]
            if delete:  # ✕ — со второго клика: первый спрашивает «удалить?»
                x = self.lbl(row, "✕", "small", MUTE)
                x.pack(side="right", padx=(0, 12))
                x.bind("<Button-1>", lambda e: delete() if x["text"] != "✕" else x.config(text=t("удалить?"), fg=RED))
                parts.append(x)
            for w in parts:
                w.bind("<Enter>", lambda e, r=row: [x.config(bg=HOVER) for x in (r, *r.winfo_children())])
                w.bind("<Leave>", lambda e, r=row: [x.config(bg=BG) for x in (r, *r.winfo_children())])
            for w in parts[:3]:
                w.bind("<Button-1>", lambda e: (m.destroy(), action()))

        for name in self.PRESET_NAMES:
            item(name == cur, self.preset_label(name), lambda n=name: self.pick_preset(n))
        self.lbl(box, t("Меняет сглаживание и эффекты.\nДальше можно подстроить ползунками."), "small", MUTE,
                 justify="left").pack(anchor="w", padx=12, pady=(4, 10))
        tk.Frame(box, height=1, bg=RULE).pack(fill="x", padx=10)
        # свои — отдельно от встроенных: заголовок, их список, а под ним поле с именем и чёрная кнопка
        self.lbl(box, t("СВОИ ПРЕСЕТЫ"), "caps", INK).pack(anchor="w", padx=12, pady=(10, 2))
        mine = [p["name"] for p in settings.my_presets(self.cfg)]
        for name in mine:
            item(cur == "my:" + name, name, lambda n=name: self.pick_preset("my:" + n),
                 delete=lambda n=name: (m.destroy(), self.delete_my_preset(n)))
        row = tk.Frame(box, bg=BG)
        row.pack(anchor="w", padx=12, pady=(6, 0))
        default = next(n for n in (t("Мой"), *(f"{t('Мой')} {i}" for i in range(2, 100))) if n not in mine)
        my_name = tk.StringVar(value=cur[3:] if cur and cur.startswith("my:") else default)
        name_box = tk.Entry(row, textvariable=my_name, width=18, relief="flat", bg=FIELD, fg=INK, insertbackground=INK,
                            font=F["body"], highlightthickness=1, highlightbackground=LINE, highlightcolor=INK)
        name_box.pack(side="left", ipady=3)
        save = lambda: (m.destroy(), self.save_my_preset(my_name.get()))
        Btn(row, t("Сохранить"), save, kind="primary", small=True).pack(side="left", padx=(8, 0))
        name_box.bind("<Return>", lambda e: save())
        self.lbl(box, t("Запомнит текущие сглаживание и эффекты.\nС тем же именем — обновит этот пресет."), "small",
                 MUTE, justify="left").pack(anchor="w", padx=12, pady=(4, 10))
        tk.Frame(box, height=1, bg=RULE).pack(fill="x", padx=10)
        item(self.accurate_blur(), t("Точный motion blur"), self.toggle_accurate)
        self.lbl(box, t("Как у камеры: курсор смазан по всей выдержке,\nа не нарисован копиями. Цена: на быстрых "
                        "рывках\nрендер до 1,6× дольше, шлейф бледнее копий."), "small", MUTE,
                 justify="left").pack(anchor="w", padx=12, pady=(4, 10))

        def focus_out(_):  # закрыть, когда фокус ушёл из меню совсем, а не в поле имени
            def check():
                if m.winfo_exists() and ((f := m.focus_get()) is None or f.winfo_toplevel() is not m):
                    m.destroy()
            m.after_idle(check)

        m.bind("<Escape>", lambda e: m.destroy())
        m.bind("<FocusOut>", focus_out)
        m.focus_force()

    def pick_preset(self, name):
        settings.apply_preset(self.cfg, name)
        settings.save(self.cfg)
        self.rebuild()  # ползунки показывают новые значения

    def save_my_preset(self, name):
        settings.save_my_preset(self.cfg, name.strip()[:24] or t("Мой"))
        settings.save(self.cfg)
        self.mark_preset()

    def delete_my_preset(self, name):
        settings.delete_my_preset(self.cfg, name)
        settings.save(self.cfg)
        self.mark_preset()
        self.preset_menu()  # меню снова открыто — видно, что пресет ушёл

    def toggle_accurate(self):
        rc, on = self.cfg["render"], not self.accurate_blur()
        rc["blur_accurate"] = on
        if on:
            rc["motion_blur"] = True  # точный блюр — тоже шлейф
        settings.save(self.cfg)
        self.rebuild()  # флажок во вкладке «Эффекты» и подпись пресета

    def tab_motion(self, p):
        l, r = self.columns(p)
        s, _ = self.section(l, "Метод сглаживания")
        self.method_frame = s.master
        self.segmented(s, "smoothing.method", {"spring": "Пружина", "one_euro": "One Euro"},
                       on_change=self.method_changed).grid(row=0, column=0, columnspan=3, sticky="w")
        self.method_hint = self.lbl(s, "", "small", MUTE)
        self.method_hint.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.spring_box, _ = self.section(l, "Пружина")
        self.slider(self.spring_box, 0, "Жёсткость", "smoothing.stiffness", 50, 1500, 10,
                    live_hint=lambda v: t("сглаживание ≈ {} мс: меньше — плавнее, больше — точнее повторяет руку. "
                                          "Задержки нет.").format(round(3380 / v ** 0.5)))  # разгон 10–90%
        self.slider(self.spring_box, 1, "Демпфирование", "smoothing.damping_ratio", 0.3, 1.5, 0.05,
                    "1 — без перелёта. Меньше — курсор слегка проскакивает цель, больше — вязко.")
        self.euro_box, _ = self.section(l, "One Euro")
        self.slider(self.euro_box, 0, "Мин. частота", "smoothing.one_euro.min_cutoff", 0.1, 5, 0.1,
                    "Гц. Меньше — сильнее сглаживаются медленные движения.")
        self.slider(self.euro_box, 1, "Бета", "smoothing.one_euro.beta", 0, 0.05, 0.001,
                    "Больше — меньше задержка на быстрых движениях.")
        g, _ = self.section(r, "Общее")
        self.slider(g, 0, "Притяжение к клику", "smoothing.click_pull_ms", 0, 400, 10,
                    "мс до и после клика: курсор приходит точно в точку клика (видно на готовом видео).")
        self.slider(g, 1, "Мёртвая зона", "smoothing.deadzone_px", 0, 10, 1,
                    "px: дрожание руки меньше этого радиуса не двигает курсор.")
        self.method_changed(self.cfg["smoothing"]["method"])

    def method_changed(self, m):
        self.method_hint.config(text=t("Мягко и «дорого», как в Screen Studio." if m == "spring" else
                                       "Почти без задержки, сглаживает в основном медленные движения."))
        self.spring_box.master.pack_forget()
        self.euro_box.master.pack_forget()
        (self.spring_box if m == "spring" else self.euro_box).master.pack(fill="x", pady=(0, 16),
                                                                           after=self.method_frame)

    def tab_effects(self, p):
        l, r = self.columns(p)
        g, h = self.section(l, "Нажатие")
        self.toggle_row(h, "включено", "render.click_animation").pack(side="right")
        self.strip = ClickStrip(g, self)
        self.strip.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 4))
        self.slider(g, 1, "Сжатие до", "render.click_scale", 0.5, 1, 0.01, "Доля размера в самой глубокой точке.")
        self.slider(g, 2, "Наклон", "render.click_tilt_deg", -45, 45, 1,
                    "Градусы против часовой вокруг кончика стрелки; минус — по часовой.", fmt=lambda v: f"{v}°")
        self.slider(g, 3, "Длительность", "render.click_ms", 100, 1000, 10,
                    "мс: первая четверть — нажатие, затем плавный возврат. Пока кнопка зажата, наклон держится.")
        g, _ = self.section(r, "Курсор")
        self.slider(g, 0, "Размер", "render.cursor_scale", 0.5, 3, 0.05,
                    "1 — как в системе, с учётом масштаба экрана и масштаба захвата в сцене OBS.")
        g, _ = self.section(r, "Наклон в движении")
        self.slider(g, 0, "Вправо", "render.motion_tilt_right_deg", -45, 90, 1,
                    "На сколько градусов курсор поворачивается вправо от положения как в Windows, когда едет вправо. "
                    "При 45° стрелка смотрит вправо так же, как в покое влево; на 45° больше, чем «Влево», — наклон "
                    "зеркальный. Минус — назад, как от инерции. 0 — выключено.",
                    fmt=lambda v: f"{v}°")
        self.slider(g, 1, "Влево", "render.motion_tilt_left_deg", -45, 45, 1,
                    "На сколько градусов курсор поворачивается влево от положения как в Windows, когда едет влево. "
                    "Минус — назад, как от инерции. 0 — выключено.",
                    fmt=lambda v: f"{v}°")
        g, h = self.section(r, "Шлейф · motion blur")
        self.toggle_row(h, "включён", "render.motion_blur").pack(side="right")
        if self.accurate_blur():  # выдержка как на камере: 1/60 — курсор смазан за 1/60 с
            name = self.lbl(g, t("Выдержка"))
            name.grid(row=0, column=0, sticky="w", pady=4)
            seg = self.segmented(g, "render.shutter", {n: f"1/{n}" for n in (30, 50, 60, 100, 120, 250, 500)})
            seg.grid(row=0, column=1, columnspan=2, sticky="w")
            self.hint_on((name, seg, *seg.winfo_children()), "Выдержка",
                         "как у камеры: курсор смазан за это время. Для «киношного» вида — вдвое короче кадра: "
                         "1/60 при 30 fps.")
        else:
            self.slider(g, 0, "Длина", "render.blur_length", 0.1, 1.5, 0.05,
                        "В долях кадра: 0.5 — как у камеры с выдержкой 180°. В покое и при анимации клика шлейфа нет.")
        if not self.accurate_blur():  # у точного блюра яркость смаза задаёт сама выдержка
            self.slider(g, 1, "Плотность", "render.blur_opacity", 0.2, 2, 0.05,
                        "Насколько заметен шлейф. В точном режиме не используется.")

        def acc_changed():
            rc = self.cfg["render"]
            if rc["blur_accurate"]:
                rc["motion_blur"] = True  # точный блюр — тоже шлейф: включаем его
            settings.save(self.cfg)
            self.rebuild()  # «Длина» ↔ «Выдержка»

        acc = self.toggle_row(g, "Точный, как у камеры — рендер дольше", "render.blur_accurate", acc_changed)
        acc.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.hint_on((acc, *acc.winfo_children()), "Точный",  # строка под превью — одна, не длиннее 720 px
                     "смаз по всей выдержке вместо копий. Цена: рендер до 1,6× дольше, шлейф бледнее, превью "
                     "упрощённое.")

    def tab_output(self, p):
        l, r = self.columns(p)
        g, _ = self.section(l, "Кодирование")
        self.lbl(g, t("Кодек")).grid(row=0, column=0, sticky="w", pady=4)
        self.segmented(g, "render.codec", {"auto": "Авто", "hevc_nvenc": "HEVC", "av1_nvenc": "AV1",
                                           "libx264": "Процессор"}).grid(row=0, column=1, columnspan=2, sticky="w")
        self.enc_lbl = self.lbl(g, "", "small", MUTE)
        self.enc_lbl.grid(row=3, column=0, columnspan=3, sticky="w", pady=(2, 0))
        self.slider(g, 1, "Качество (CQ)", "render.cq", 10, 35, 1,
                    "Меньше — лучше и тяжелее файл; 18 — почти без потерь.")
        self.lbl(g, t("Пресет")).grid(row=2, column=0, sticky="w", pady=4)
        self.segmented(g, "render.preset", {f"p{i}": str(i) for i in range(1, 8)}).grid(
            row=2, column=1, columnspan=2, sticky="w")
        g, _ = self.section(l, "Дополнительно")
        self.toggle_row(g, "Отладка: поверх — реальный курсор (красный)", "render.debug_raw").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=3)
        self.toggle_row(g, "Траектория в CSV (Fusion / After Effects)", "render.export_keyframes").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=3)
        g, _ = self.section(r, "Синхронизация с видео")
        for i, (key, text) in enumerate(((30, "Сдвиг · 30 fps"), (60, "Сдвиг · 60 fps"), ("default", "Другой fps"))):
            self.slider(g, i, text, f"sync.offset_ms.{key}", -200, 200, 1,
                        "мс. Курсор опережает картинку — уменьшите, отстаёт — увеличьте. Проверять удобно с "
                        "отладкой.", fmt=lambda v: f"{v:+d}")
        g, _ = self.section(r, "ffmpeg")
        e, self.ffmpeg_var = self.entry(g, "render.ffmpeg", width=40)
        e.grid(row=0, column=0, columnspan=2, sticky="ew", ipady=4)
        for ev in ("<FocusOut>", "<Return>"):
            e.bind(ev, lambda _: self.redetect_encoder())
        Btn(g, t("Обзор…"), self.choose_ffmpeg, small=True).grid(row=0, column=2, sticky="w", padx=(8, 0))

    def tab_obs(self, p):
        l, r = self.columns(p)
        g, _ = self.section(l, "Подключение")
        for i, (text, path, kw) in enumerate((("Адрес", "obs.host", {"width": 18}),
                                              ("Порт", "obs.port", {"conv": int, "width": 8}),
                                              ("Пароль", "obs.password", {"show": "•", "width": 18}))):
            self.lbl(g, t(text)).grid(row=i, column=0, sticky="w", pady=3)
            self.entry(g, path, **kw)[0].grid(row=i, column=1, sticky="w", ipady=3)
        Btn(g, t("Подключиться"), self.connect, small=True).grid(row=0, column=2, sticky="e")
        self.lbl(g, t("OBS → Сервис → Настройки сервера WebSocket"), "small", MUTE).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        g, _ = self.section(l, "Горячая клавиша")
        self.lbl(g, t("Старт и стоп")).grid(row=0, column=0, sticky="w", pady=3)
        row = tk.Frame(g, bg=BG)
        row.grid(row=0, column=1, columnspan=2, sticky="w")
        e, self.hotkey_var = self.entry(row, "hotkey", width=14, live=False)
        e.pack(side="left", ipady=3)
        e.bind("<Return>", lambda _: self.apply_hotkey(self.hotkey_var.get()))
        Btn(row, t("Применить"), lambda: self.apply_hotkey(self.hotkey_var.get()), small=True).pack(side="left",
                                                                                                    padx=10)
        self.lbl(row, "F9, ctrl+shift+R, alt+F10", "small", MUTE).pack(side="left")

        g, h = self.section(r, "Что записывать")
        self.display_seg = self.segmented(h, "display", {"auto": "Авто — по сцене OBS"},
                                          on_change=lambda v: self.pick_monitor(v))
        self.display_seg.pack(side="right")
        self.picker = MonitorPicker(g, self, self.pick_monitor)
        self.picker.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.toggle_row(g, "Выключать захват курсора в источниках сцены", "obs.disable_capture_cursor").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))

    def pick_monitor(self, name):
        self.cfg["display"] = name
        self.schedule_save()
        for b in self.display_seg.winfo_children():
            b.selected = name == "auto"
            b.draw()
        self.picker.draw()
        self.check_display()

    # ---------- OBS и запись ----------
    def set_state(self, state):
        self.state = state
        self.rec_btn.set_text({"offline": t("Подключить OBS"), "busy": "…", "idle": "●  " + t("Начать запись"),
                               "recording": "■  " + t("Остановить")}[state])
        self.rec_btn.set_enabled(state != "busy")
        self.key_lbl.config(text=self.cfg["hotkey"])
        if state != "recording":
            self.time_lbl.config(text="00:00", fg=LINE)
            self.rec_dot.config(fg=CARD)
        else:
            self.time_lbl.config(fg=INK)
        if state == "offline" and self.spec.get("obs") != "offline":
            self.set_spec(obs="offline")

    def set_spec(self, **kw):
        """Строки «Сейчас в OBS». Храним сырые значения, текст собираем на текущем языке."""
        if kw.get("obs") == "offline":
            self.spec = {}
        self.spec = {**self.spec, **kw}
        s = self.spec
        obs_line = {"offline": t("OBS — нет связи"), "connecting": t("OBS — подключение…")}.get(
            s.get("obs"), t("OBS {} — подключено").format(s.get("version", "")))
        if s.get("missing"):
            scene_line = t("В сцене OBS нет захвата экрана")
        elif "scene" in s:
            scene_line = t("Сцена «{}» · экран {}").format(s["scene"], s["display"])
        else:
            scene_line = t("Сцена —")
        canvas_line = t("{} · профиль «{}»").format(s["canvas"], s["profile"]) if "canvas" in s else t("Холст —")
        for w, text, bad in zip(self.now, (obs_line, scene_line, canvas_line),
                                (s.get("obs") == "offline", s.get("missing"), False)):
            w.config(text="•  " + text, fg=RED if bad else INK)

    def refresh_obs(self):
        if self.recorder:
            self.check_display()
        else:
            self.connect()

    def connect(self):
        if self.recorder and self.recorder.recording:
            return messagebox.showwarning(APP, t("Идёт запись — сначала остановите её."))
        self.set_state("busy")
        self.set_spec(obs="connecting")

        def work():
            if self.recorder:
                self.recorder.close()
                self.recorder = None
            try:
                r = Recorder(self.cfg)
                v = r.obs.req.get_version()
                if self.folder_pending:  # папку выбрали, пока OBS был недоступен
                    self._set_obs_folder(self.cfg["ui"]["folder"], r)
                folder = str(Path(r.obs.req.get_record_directory().record_directory))
            except Exception as e:
                log.error("%s", e)
                self.last_error = str(e)
                return self.post(self.set_state, "offline")
            self.recorder = r
            self.post(self.on_online, v.obs_version, folder)
            self._check_display()

        self.worker.submit(work)

    def on_online(self, version, folder):
        self.set_spec(obs="online", version=version)
        if self.cfg["ui"]["folder"] != folder:  # папку записей задаёт OBS
            self.cfg["ui"]["folder"] = folder
            self.schedule_save()
        self.refresh_list()
        self.set_state("idle")

    def _set_obs_folder(self, folder, r=None):
        try:
            (r or self.recorder).obs.req.set_record_directory(folder)
            self.folder_pending = False
            log.info(t("OBS теперь сохраняет записи в %s"), folder)
        except Exception as e:
            log.error(t("Не удалось сменить папку записей в OBS (нужен OBS 30 или новее): %s"), e)

    def check_display(self):
        if self.recorder:
            self.worker.submit(self._check_display)

    def _check_display(self):
        try:
            d = self.recorder.pick_display()
            disp = d["device"].split(chr(92))[-1].replace("DISPLAY", "")
            if abs(d["zoom"] - 1) > 0.005:
                disp += f" ×{d['zoom']:.2f}"
            self.post(self.on_detected, d["device"], d["fps"], profile=d["profile"], scene=d["scene"], display=disp,
                      canvas=f"{d['canvas'][0]}×{d['canvas'][1]} · {d['fps']:g} fps", missing=False)
        except LookupError as e:
            log.warning("%s", str(e).split("\n")[0])
            self.post(self.on_detected, None, None, missing=True)
        except Exception as e:
            log.error("%s", e)

    def on_detected(self, device, fps, **spec):
        self.detected, self.last_fps = device, fps
        self.set_spec(**spec)
        self.picker.draw()

    def toggle(self):
        if self.state == "busy":
            return
        if not self.recorder:
            return self.connect()
        self.set_state("busy")
        self.worker.submit(self._toggle)

    def _toggle(self):
        r = self.recorder
        try:
            if r.recording:
                self.post(self.on_stopped, r.stop())
            else:
                r.start()
                self.post(self.on_started)
            return
        except Exception as e:
            log.error(t("Ошибка: %s"), e)
        try:
            r.obs.req.get_version()
            self.post(self.set_state, "recording" if r.recording else "idle")
        except Exception:
            self.recorder = None
            self.post(self.set_state, "offline")

    def on_started(self):
        self.rec_t0 = time.time()
        self.set_state("recording")

    def on_stopped(self, parts):
        self.set_state("idle")
        self.refresh_list()
        for video, log_path in parts:
            if video and self.cfg["ui"]["auto_render"]:
                self.enqueue(video, log_path)

    def apply_hotkey(self, new=None, initial=False):
        """Регистрирует горячую клавишу; в настройки она попадает, только если заработала."""
        new = (new or self.cfg["hotkey"]).strip()
        try:
            parse_hotkey(new)
        except (ValueError, KeyError, IndexError):
            return messagebox.showerror(APP, t("Непонятная горячая клавиша: {}").format(new))
        old = self.hotkey
        if old and old.hotkey == new and not initial:
            return
        if old:
            old.stop()
        try:
            self.hotkey = HotkeyThread(new, lambda: self.post(self.toggle)).start()
            log.info(t("Горячая клавиша: %s"), new)
            if new != self.cfg["hotkey"]:
                self.cfg["hotkey"] = new
                self.schedule_save()
        except RuntimeError as e:
            self.hotkey = None
            log.error("%s", e)
            if not initial:
                messagebox.showerror(APP, str(e).capitalize())
            if old:
                self.hotkey = HotkeyThread(old.hotkey, lambda: self.post(self.toggle)).start()
        self.set_state(self.state)

    # ---------- записи и рендер ----------
    def folder(self):
        return Path(self.cfg["ui"]["folder"] or Path.home() / "Videos")

    def choose_folder(self):
        d = filedialog.askdirectory(initialdir=self.folder(), title=t("Куда OBS будет сохранять записи"))
        if not d:
            return
        d = str(Path(d))
        self.cfg["ui"]["folder"] = d
        self.schedule_save()
        self.refresh_list()
        if self.recorder:
            self.worker.submit(self._set_obs_folder, d)
        else:
            self.folder_pending = True
            log.info(t("Папка записей: %s — OBS переключится на неё при подключении"), d)

    def choose_ffmpeg(self):
        f = filedialog.askopenfilename(title="ffmpeg.exe", filetypes=[("ffmpeg", "ffmpeg.exe"), (t("Все"), "*.*")])
        if f:
            self.ffmpeg_var.set(f)
            self.redetect_encoder()

    def redetect_encoder(self):
        """Путь к ffmpeg поменяли — заново узнать, чем кодировать (только если путь и правда другой)."""
        if self.cfg["render"]["ffmpeg"] != self.encoder_src:
            self.encoder_src = self.cfg["render"]["ffmpeg"]
            self.renderer.submit(self._detect_encoder)

    def refresh_list(self):
        folder = self.folder()
        self.folder_lbl.config(text=str(folder))
        keep = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        logs = sorted(folder.glob("*.cursor.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for lp in logs:
            stem = lp.name[:-len(".cursor.json")]
            smooth = folder / f"{stem}_smooth.mp4"
            self.tree.insert("", "end", iid=str(lp), text=stem, values=(
                time.strftime("%d.%m.%Y  %H:%M", time.localtime(lp.stat().st_mtime)),
                t("готово") if smooth.exists() else "—"))
        sel = [i for i in keep if self.tree.exists(i)] or ([str(logs[0])] if logs else [])
        self.tree.selection_set(sel)

    def selected(self):
        out = []
        for iid in self.tree.selection():
            try:
                out.append(rerender_paths([iid]))
            except FileNotFoundError as e:
                log.error("%s", e)
        if not out and not self.tree.selection():
            messagebox.showinfo(APP, t("Выберите запись в списке."))
        return out

    def render_selected(self):
        for video, lp in self.selected():
            self.enqueue(video, lp)

    def preview_selected(self):
        sel = self.selected()
        if sel:
            self.enqueue(*sel[0], clip=(self.cfg["ui"]["preview_start"], self.cfg["ui"]["preview_len"]))

    def open_selected(self, result):
        for video, _ in self.selected()[:1]:
            out = video.with_name(video.stem + "_smooth.mp4")
            if result and not out.exists():
                return messagebox.showinfo(APP, t("Эта запись ещё не отрендерена — нажмите «Рендер»."))
            os.startfile(out if result else video)

    def show_selected(self):
        for video, _ in self.selected()[:1]:
            out = video.with_name(video.stem + "_smooth.mp4")
            subprocess.Popen(["explorer", "/select,", str(out if out.exists() else video)])

    def enqueue(self, video, log_path, clip=None):
        self.pending += 1
        self.prog_text.config(text=t("в очереди: {}").format(self.pending), fg=INK2)
        self.renderer.submit(self._render, video, log_path, copy.deepcopy(self.cfg), clip)

    def _render(self, video, log_path, cfg, clip):
        self.cancel = False
        self.post(self.prog_text.config, {"text": f"{t('превью') if clip else t('рендер')} · {video.stem}", "fg": INK})
        try:
            out = render.render(video, log_path, cfg, progress=self._progress, clip=clip)
            if clip:
                os.startfile(out)
        except Exception as e:
            log.error("%s: %s", t("Превью не удалось") if clip else t("Рендер не удался"), e)
        finally:
            self.post(self.render_done)

    def _progress(self, pct):
        self.post(self.set_progress, pct)
        return self.cancel

    def set_progress(self, pct):
        self.gauge.set(pct)
        self.prog_pct.config(text=f"{min(pct, 100):.0f}%")

    def render_done(self):
        self.pending -= 1
        self.refresh_list()
        if not self.pending:
            self.prog_text.config(text=t("нет задач"), fg=MUTE)
            self.set_progress(0)

    def cancel_render(self):
        self.cancel = True

    def append_log(self, text, level):
        tag = "err" if level >= logging.ERROR else "warn" if level >= logging.WARNING else ""
        self.log_lines.append((text, tag))
        self.last_log.config(text=text.split("\n")[0][:110], fg=RED if tag == "err" else "#A15C00" if tag else MUTE)
        if self.log_win and self.log_win.winfo_exists():
            self._log_insert(text, tag)

    def _log_insert(self, text, tag):
        box = self.log_win.text
        box.configure(state="normal")
        box.insert("end", text + "\n", tag)
        box.see("end")
        box.configure(state="disabled")

    def open_log(self):
        if self.log_win and self.log_win.winfo_exists():
            return self.log_win.lift()
        w = self.log_win = tk.Toplevel(self.root, bg=CARD)
        w.title(f"{APP} — " + t("журнал"))
        w.geometry("900x420")
        self.lbl(w, t("ЖУРНАЛ"), "caps", INK2).pack(anchor="w", padx=28, pady=(22, 8))
        w.text = tk.Text(w, wrap="word", relief="flat", bg=FIELD, fg=INK2, font=F["mono_s"], padx=12, pady=8,
                         highlightthickness=1, highlightbackground=RULE)
        w.text.pack(fill="both", expand=True, padx=28, pady=(0, 24))
        w.text.tag_configure("warn", foreground="#A15C00")
        w.text.tag_configure("err", foreground=RED)
        for text, tag in self.log_lines:
            self._log_insert(text, tag)

    def on_close(self):
        if self.recorder and self.recorder.recording:
            if not messagebox.askyesno(APP, t("Идёт запись. Остановить её и выйти?")):
                return
            try:
                self.recorder.stop()  # лог сохранится; отрендерить можно при следующем запуске
            except Exception as e:
                log.error("%s", e)
        if self.pending and not messagebox.askyesno(APP, t("Идёт рендер. Прервать и выйти?")):
            return
        self.cancel = True
        if self._save_job:
            settings.save(self.cfg)
        if self.hotkey:
            self.hotkey.stop()
        if self.recorder:
            self.recorder.close()
        self.root.destroy()
        os._exit(0)  # не ждать фоновые потоки (рендер уже отменён)


def main():
    load_fonts()
    # Окно программы масштабирует сама Windows (GDI scaling): под масштаб монитора и сразу при переносе на другой,
    # текст и линии чёткие. Только для этого потока — запись, рендер и хоткей идут в настоящих пикселях.
    wc.user32.SetThreadDpiAwarenessContext(C.c_void_p(-5))  # DPI_AWARENESS_CONTEXT_UNAWARE_GDISCALED
    root = tk.Tk()
    root.tk.call("tk", "scaling", 96 / 72)  # вёрстка в «пикселях при 100%», дальше растягивает Windows
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
