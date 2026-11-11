"""Linux: мониторы, логгер курсора, хоткей, спрайты тем Xcursor. Тот же набор имён, что в win32cursor.

X11 — всё напрямую через libX11. На Wayland программе не дают видеть чужой курсор и ввод, поэтому там:
положение и вид курсора — из портала ScreenCast (waylandcursor.py), клики и хоткей — из /dev/input (нужна группа input).
Окно программы и список мониторов и на Wayland идут через XWayland. Библиотеки грузятся при первом вызове:
без них модуль импортируется, поэтому разбор файлов и событий проверяется и на Windows (selftest.py)."""
import configparser
import contextlib
import ctypes as C
import glob
import logging
import os
import re
import select
import struct
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import cursorcore
from cursorcore import corner_pos  # noqa: F401 — wc.corner_pos зовёт app.pyw
from i18n import t
from linuxautostart import running_exe  # noqa: F401 — wc.running_exe зовут ae.py и premiere.py

log = logging.getLogger("smooth")
user32 = None  # только для кода под Windows: виртуальный монитор, проверка курсора в кадре
WAYLAND = os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))

# Системные курсоры: имя -> имена файла в теме Xcursor (новые и старые). Индекс 0 (arrow) — фолбэк.
CURSORS = {
    "arrow": ("default", "left_ptr", "arrow"), "ibeam": ("text", "xterm", "ibeam"), "wait": ("wait", "watch"),
    "cross": ("crosshair", "cross", "tcross"), "size_nwse": ("nwse-resize", "bd_double_arrow", "size_fdiag"),
    "size_nesw": ("nesw-resize", "fd_double_arrow", "size_bdiag"),
    "size_we": ("ew-resize", "sb_h_double_arrow", "size_hor"), "size_ns": ("ns-resize", "sb_v_double_arrow", "size_ver"),
    "size_all": ("all-scroll", "fleur", "move", "size_all"), "no": ("not-allowed", "crossed_circle", "forbidden"),
    "hand": ("pointer", "hand2", "hand1", "pointing_hand"), "busy": ("progress", "left_ptr_watch", "half-busy"),
    "help": ("help", "question_arrow", "whats_this"),
}
TYPES = list(CURSORS)
BY_XNAME = {x: n for n, names in CURSORS.items() for x in names}

PIPEWIRE = ("pipewire-screen-capture-source", "pipewire-desktop-capture-source")
MONITOR_KINDS = ("xshm_input", *PIPEWIRE)  # какие источники OBS захватывают экран


@contextlib.contextmanager
def physical():
    """На Linux координаты окна и экрана и так одни: масштабирования окна, как на Windows, нет."""
    yield


# ---------- X11 ----------
class XFixesCursorImage(C.Structure):
    _fields_ = [("x", C.c_short), ("y", C.c_short), ("width", C.c_ushort), ("height", C.c_ushort),
                ("xhot", C.c_ushort), ("yhot", C.c_ushort), ("cursor_serial", C.c_ulong),
                ("pixels", C.POINTER(C.c_ulong)), ("atom", C.c_ulong), ("name", C.c_char_p)]


class XRRMonitorInfo(C.Structure):
    _fields_ = [("name", C.c_ulong), ("primary", C.c_int), ("automatic", C.c_int), ("noutput", C.c_int),
                ("x", C.c_int), ("y", C.c_int), ("width", C.c_int), ("height", C.c_int),
                ("mwidth", C.c_int), ("mheight", C.c_int), ("outputs", C.c_void_p)]


class XKeyEvent(C.Structure):
    _fields_ = [("type", C.c_int), ("pad", C.c_byte * 188)]  # весь XEvent — 192 байта, нужен только type


_libs = None
_x_errors = []


def x11():
    """(libX11, libXfixes, libXrandr), настроенные; RuntimeError — нет библиотек или сервера."""
    global _libs
    if _libs is None:
        try:
            x, xf, xr = C.CDLL("libX11.so.6"), C.CDLL("libXfixes.so.3"), C.CDLL("libXrandr.so.2")
        except OSError as e:
            raise RuntimeError(t("Нет библиотек X11 ({}). Поставьте libx11-6, libxfixes3 и libxrandr2").format(e))
        v, u, i = C.c_void_p, C.c_ulong, C.POINTER(C.c_int)
        x.XOpenDisplay.argtypes, x.XOpenDisplay.restype = [C.c_char_p], v
        x.XCloseDisplay.argtypes = [v]
        x.XDefaultRootWindow.argtypes, x.XDefaultRootWindow.restype = [v], u
        x.XQueryPointer.argtypes = [v, u, C.POINTER(u), C.POINTER(u), i, i, i, i, C.POINTER(C.c_uint)]
        x.XGetAtomName.argtypes, x.XGetAtomName.restype = [v, u], v
        x.XFree.argtypes = [v]
        x.XInternAtom.argtypes, x.XInternAtom.restype = [v, C.c_char_p, C.c_int], u
        x.XGetSelectionOwner.argtypes, x.XGetSelectionOwner.restype = [v, u], u
        x.XKeysymToKeycode.argtypes, x.XKeysymToKeycode.restype = [v, u], C.c_ubyte
        x.XGrabKey.argtypes = [v, C.c_int, C.c_uint, u, C.c_int, C.c_int, C.c_int]
        x.XUngrabKey.argtypes = [v, C.c_int, C.c_uint, u]
        x.XSync.argtypes = [v, C.c_int]
        x.XkbSetDetectableAutoRepeat.argtypes = [v, C.c_int, C.c_void_p]
        x.XPending.argtypes = [v]
        x.XNextEvent.argtypes = [v, C.POINTER(XKeyEvent)]
        xf.XFixesGetCursorImage.argtypes = [v]
        xf.XFixesGetCursorImage.restype = C.POINTER(XFixesCursorImage)
        xr.XRRGetMonitors.argtypes = [v, u, C.c_int, i]
        xr.XRRGetMonitors.restype = C.POINTER(XRRMonitorInfo)
        xr.XRRFreeMonitors.argtypes = [C.POINTER(XRRMonitorInfo)]
        x.XInitThreads()
        # стандартный обработчик завершает программу, а занятый хоткей — обычная ошибка
        handler = C.CFUNCTYPE(C.c_int, v, v)(lambda d, e: _x_errors.append(1) or 0)
        x.XSetErrorHandler.argtypes = [type(handler)]
        x.XSetErrorHandler(handler)
        _libs = x, xf, xr, handler  # обработчик живёт, пока жив процесс
    return _libs[:3]


def open_display():
    x = x11()[0]
    d = x.XOpenDisplay(None)
    if not d:
        raise RuntimeError(t("Не могу подключиться к X-серверу. На Wayland нужен XWayland"))
    return d


def argb_image(words, w, h):
    """Пиксели курсора X11 (ARGB с умножением на альфу, по слову на пиксель) → RGBA без умножения."""
    a = np.asarray(words).astype(np.uint32).reshape(h, w)  # слово X11 — unsigned long, важны младшие 32 бита
    ch = [((a >> s) & 0xFF).astype(float) for s in (16, 8, 0, 24)]
    k = 255 / np.maximum(ch[3], 1)
    rgb = [np.clip(c * k, 0, 255) for c in ch[:3]]
    return Image.fromarray(np.dstack([*rgb, ch[3]]).round().astype(np.uint8), "RGBA")


class X11Source:
    """Курсор на X11. read() — как у WaylandSource: (x, y, вид, виден ли, левая кнопка, правая кнопка)."""

    def __init__(self, left=0, top=0):
        self.x, self.xf, _ = x11()
        self.dpy = open_display()
        self.root = self.x.XDefaultRootWindow(self.dpy)
        self.left, self.top = left, top
        self.shapes, self.hidden = {}, set()  # серийный номер курсора → (имя системного вида, спрайт)

    def read(self):
        u, i, mask = C.c_ulong(), C.c_int(), C.c_uint()
        px, py = C.c_int(), C.c_int()
        self.x.XQueryPointer(self.dpy, self.root, C.byref(u), C.byref(u), C.byref(px), C.byref(py), C.byref(i),
                             C.byref(i), C.byref(mask))
        img = self.xf.XFixesGetCursorImage(self.dpy)
        serial = None
        if img:
            c = img.contents
            serial = c.cursor_serial
            if serial not in self.shapes:
                self._remember(c)
            self.x.XFree(img)
        return (px.value - self.left, py.value - self.top, serial, serial not in self.hidden,
                bool(mask.value & 0x100), bool(mask.value & 0x400))  # Button1Mask, Button3Mask

    def _remember(self, c):
        n = c.width * c.height
        words = np.ctypeslib.as_array(c.pixels, (n,)).copy() if n else np.zeros(0, np.uint64)
        sprite = (argb_image(words, c.width, c.height), (c.xhot, c.yhot)) if n else None
        if not sprite or not sprite[0].getchannel("A").getbbox():
            self.hidden.add(c.cursor_serial)  # пустой курсор: так программы его и прячут
        self.shapes[c.cursor_serial] = (BY_XNAME.get((c.name or b"").decode("utf-8", "replace")), sprite)

    def describe(self, shape):
        return self.shapes.get(shape, (None, None))

    def close(self):
        self.x.XCloseDisplay(self.dpy)


def list_monitors():
    x, _, xr = x11()
    dpy, n = open_display(), C.c_int()
    try:
        arr = xr.XRRGetMonitors(dpy, x.XDefaultRootWindow(dpy), 1, C.byref(n))
        out = []
        for i in range(n.value):
            m = arr[i]
            p = x.XGetAtomName(dpy, m.name)
            name = C.string_at(p).decode("utf-8", "replace") if p else f"screen{i}"
            x.XFree(p)
            out.append({"device": name, "id": str(i), "left": m.x, "top": m.y, "primary": bool(m.primary),
                        "work": [m.x, m.y, m.x + m.width, m.y + m.height], "width": m.width, "height": m.height,
                        "dpi": 96, "virtual": False})
        xr.XRRFreeMonitors(arr)
        return out
    finally:
        x.XCloseDisplay(dpy)


def primary(mons=None):
    mons = mons or list_monitors()
    return next((m for m in mons if m.get("primary")), None) or cursorcore.primary(mons)


def find_display(select="auto", obs_ids=()):
    return cursorcore.find_display(list_monitors(), select, obs_ids)


def find_window(part):
    return None


def has_tray():
    """Есть ли у рабочего стола трей: значок pystray встаёт в него через X11."""
    x, dpy = x11()[0], open_display()
    try:
        return bool(x.XGetSelectionOwner(dpy, x.XInternAtom(dpy, b"_NET_SYSTEM_TRAY_S0", 0)))
    finally:
        x.XCloseDisplay(dpy)


def work_area():
    """Панели рабочего стола Tk не видит: берём весь основной экран, окно поправит оконный менеджер."""
    m = primary()
    return m["left"], m["top"], m["left"] + m["width"], m["top"] + m["height"]


# ---------- OBS ----------
def capture_id(kind, settings, transform, mons=None):
    """Какой монитор снимает источник OBS: id как в list_monitors. У захвата PipeWire экран выбирают в окне системы,
    OBS знает только размер картинки: ищем монитор с таким размером, затем с такими пропорциями (масштаб экрана)."""
    if kind == "xshm_input":
        return str(settings.get("screen", 0))
    mons = mons or list_monitors()
    w, h = transform["sourceWidth"], transform["sourceHeight"]
    same = [m for m in mons if (m["width"], m["height"]) == (w, h)] or \
        [m for m in mons if h and abs(m["width"] / m["height"] - w / h) < 0.01]
    return (same[0] if same else primary(mons))["id"]


def adjust_display(d, transform):
    """Экран с масштабом: XWayland сообщает логический размер, а видео и координаты портала — в настоящих пикселях."""
    w, h = int(transform["sourceWidth"]), int(transform["sourceHeight"])
    if w and h and (w, h) != (d["width"], d["height"]):
        d["dpi"] = round(96 * w / d["width"])
        d["width"], d["height"] = w, h


def capture_input(d, cursor, kinds):
    """(вид источника OBS, настройки) для захвата экрана d."""
    if WAYLAND:
        kind = next((k for k in PIPEWIRE if k in kinds), None)
        if not kind:
            raise RuntimeError(t("В этой сборке OBS нет захвата экрана через PipeWire"))
        return kind, {"ShowCursor": cursor}
    return "xshm_input", {"screen": int(d["id"]), "show_cursor": cursor}


# ---------- /dev/input: клики и хоткей на Wayland ----------
EVENT = struct.Struct("llHHi")  # struct input_event: время, тип, код, значение
EV_KEY, BTN_LEFT, BTN_RIGHT = 1, 0x110, 0x111
MODS = {"ctrl": (29, 97), "shift": (42, 54), "alt": (56, 100), "win": (125, 126)}
KEYS = {**{f"f{i}": 58 + i for i in range(1, 11)}, "f11": 87, "f12": 88, "0": 11,
        **{str(i): i + 1 for i in range(1, 10)},
        **dict(zip("qwertyuiop", range(16, 26))), **dict(zip("asdfghjkl", range(30, 39))),
        **dict(zip("zxcvbnm", range(44, 51)))}


def key_events(data):
    """Нажатия и отпускания из байтов /dev/input: (код, 1/0). Автоповтор (2) не считаем."""
    n = len(data) - len(data) % EVENT.size
    return [(code, value) for _, _, kind, code, value in EVENT.iter_unpack(data[:n]) if kind == EV_KEY and value < 2]


class InputWatch(threading.Thread):
    """Читает все доступные устройства /dev/input. Устройства подключают на ходу, поэтому список обновляется."""

    def __init__(self):
        super().__init__(daemon=True)
        self.down, self.listeners = set(), []

    def run(self):
        fds, scanned = {}, 0
        while True:
            if time.monotonic() - scanned > 5:
                scanned = time.monotonic()
                for path in set(glob.glob("/dev/input/event*")) - set(fds.values()):
                    with contextlib.suppress(OSError):
                        fds[os.open(path, os.O_RDONLY | os.O_NONBLOCK)] = path
            for fd in select.select(list(fds), [], [], 1)[0]:
                try:
                    data = os.read(fd, EVENT.size * 64)
                except OSError:  # устройство отключили
                    os.close(fd)
                    del fds[fd]
                    continue
                for code, value in key_events(data):
                    (self.down.add if value else self.down.discard)(code)
                    if value:
                        for f in list(self.listeners):
                            f(code)


_input = None


def input_watch():
    """Общий поток чтения /dev/input; RuntimeError — устройства закрыты."""
    global _input
    if not any(os.access(p, os.R_OK) for p in glob.glob("/dev/input/event*")):
        raise RuntimeError(t("Нет доступа к /dev/input. Выполните: sudo usermod -aG input $USER — и войдите заново"))
    if _input is None:
        _input = InputWatch()
        _input.start()
    return _input


def parse_hotkey(s):
    *mods, key = s.lower().replace(" ", "").split("+")
    for m in mods:
        MODS[m]  # noqa: B018 — KeyError на неизвестном модификаторе, как на Windows
    if key not in KEYS:
        raise ValueError(t("непонятный хоткей: {}").format(s))
    return frozenset(mods), key


class HotkeyThread(threading.Thread):
    """Глобальный хоткей, callback — из этого потока: XGrabKey на X11, /dev/input на Wayland."""
    MASKS = {"shift": 1, "ctrl": 4, "alt": 8, "win": 0x40}
    LOCKS = (0, 2, 0x10, 0x12)  # Caps Lock, Num Lock: иначе с ними хоткей не сработает

    def __init__(self, hotkey, callback, needed=None):  # needed — только для Windows
        super().__init__(daemon=True)
        self.hotkey, (self.mods, self.key) = hotkey, parse_hotkey(hotkey)
        self.callback, self.error = callback, None
        self._ready, self._stop_it = threading.Event(), threading.Event()

    def start(self):
        if WAYLAND:
            input_watch().listeners.append(self._pressed)
            return self
        super().start()
        self._ready.wait()
        if self.error:
            raise RuntimeError(self.error)
        return self

    def _pressed(self, code):
        if code == KEYS[self.key] and all(set(MODS[m]) & _input.down for m in self.mods):
            self.callback()

    def run(self):
        x = x11()[0]
        dpy = open_display()
        root = x.XDefaultRootWindow(dpy)
        # удержание клавиши: X11 повторяет только нажатия, без отпусканий — так их можно отличить от новых нажатий
        x.XkbSetDetectableAutoRepeat(dpy, 1, None)
        sym = 0xFFBD + int(self.key[1:]) if len(self.key) > 1 else ord(self.key)  # XK_F1 = 0xFFBE
        code = x.XKeysymToKeycode(dpy, sym)
        mask = sum(self.MASKS[m] for m in self.mods)
        _x_errors.clear()
        for lock in self.LOCKS:
            x.XGrabKey(dpy, code, mask | lock, root, 1, 1, 1)  # GrabModeAsync
        x.XSync(dpy, 0)
        if _x_errors:
            self.error = t("хоткей {} уже занят другой программой").format(self.hotkey)
        self._ready.set()
        ev, held = XKeyEvent(), False
        while not self.error and not self._stop_it.wait(0.02):
            while x.XPending(dpy):
                x.XNextEvent(dpy, C.byref(ev))
                if ev.type == 2 and not held:  # KeyPress; пока держат — это автоповтор, как MOD_NOREPEAT на Windows
                    held = True
                    self.callback()
                elif ev.type == 3:  # KeyRelease
                    held = False
        for lock in self.LOCKS:
            x.XUngrabKey(dpy, code, mask | lock, root)
        x.XCloseDisplay(dpy)

    def stop(self):
        if WAYLAND:
            with contextlib.suppress(ValueError):
                _input.listeners.remove(self._pressed)
        elif self.is_alive():
            self._stop_it.set()
            self.join(2)


# ---------- логгер ----------
class CursorLogger(cursorcore.BaseLogger):
    def __init__(self, display, hz=240):
        super().__init__(display, hz, TYPES, cursor_base_size() * display["dpi"] / 96)
        self.source = None

    def start(self):
        """На Wayland система спросит, какой экран отдать (один раз, дальше помнит), поэтому ждём ответа до записи."""
        if WAYLAND:
            import waylandcursor
            self.source = waylandcursor.WaylandSource(self.display)
        else:
            self.source = X11Source(self.display["left"], self.display["top"])
        super().start()

    def stop(self):
        super().stop()
        if self.source:
            self.source.close()

    def _type(self, shape, seen):
        name, sprite = self.source.describe(shape)
        if name:
            return TYPES.index(name)
        return self.add_custom(cursorcore.pack_snapshot(sprite, self.ref) if sprite else None, seen)

    def _run(self):
        shapes, seen, prev = {}, {}, {"L": False, "R": False}
        nxt = time.perf_counter_ns()
        while not self._stop.is_set():
            now = time.perf_counter_ns()
            x, y, shape, showing, left, right = self.source.read()
            k = shapes.get(shape)
            if k is None:
                k = shapes[shape] = self._type(shape, seen)
            self.samples.append((now, x, y, k, int(showing)))
            for b, down in (("L", left), ("R", right)):
                if down != prev[b]:
                    self.clicks.append((now, b, int(down), x, y))
                    prev[b] = down
            nxt = self.wait_tick(nxt)


# ---------- для окна программы ----------
_gui = None


def pointer():
    """(x, y, курсор, нажата ли левая кнопка, виден ли) сейчас. На Wayland курсор виден, только пока он над окнами
    XWayland: живое превью стоит, а на запись это не влияет."""
    global _gui
    _gui = _gui or X11Source()
    x, y, shape, showing, left, _ = _gui.read()
    return x, y, shape, left, showing


def system_shapes():
    """Спрайт любого курсора снимается с экрана, заранее сопоставлять не с чем."""
    return {}


def snapshot(shape, ref):
    sprite = _gui.describe(shape)[1] if _gui else None
    return cursorcore.pack_snapshot(sprite, ref) if sprite else None


# ---------- темы курсоров ----------
def _gsettings(key):
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        r = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", key], capture_output=True, text=True,
                           timeout=2)
        return r.stdout.strip().strip("'") if r.returncode == 0 else ""
    return ""


def cursor_base_size():
    """Размер курсора в теме при 100%."""
    for v in (os.environ.get("XCURSOR_SIZE"), _gsettings("cursor-size")):
        if v and v.isdigit() and int(v) > 0:
            return int(v)
    return 24


def theme_dirs():
    path = os.environ.get("XCURSOR_PATH") or \
        "~/.local/share/icons:~/.icons:/usr/share/icons:/usr/share/pixmaps:/usr/local/share/icons"
    return [Path(p).expanduser() for p in path.split(":") if p]


def _inherits(theme, dirs):
    for d in dirs:
        for f in ("index.theme", "cursor.theme"):
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            with contextlib.suppress(OSError, configparser.Error, UnicodeDecodeError):
                cp.read(d / theme / f, encoding="utf-8")
                if cp.has_option("Icon Theme", "Inherits"):
                    return re.split(r"[,;:\s]+", cp.get("Icon Theme", "Inherits").strip())
    return []


def find_cursor(names, theme, dirs, seen=None):
    """Файл курсора по его именам: в теме, затем в темах, от которых она унаследована."""
    seen = seen if seen is not None else set()
    if not theme or theme in seen:
        return None
    seen.add(theme)
    for d in dirs:
        for n in names:
            if (d / theme / "cursors" / n).is_file():
                return d / theme / "cursors" / n
    for parent in _inherits(theme, dirs):
        if found := find_cursor(names, parent, dirs, seen):
            return found
    return None


def read_xcursor(data, size):
    """Файл Xcursor → (RGBA, hotspot, номинальный размер): наименьшая картинка не меньше size, иначе самая крупная.
    У анимированного курсора — первый кадр."""
    if data[:4] != b"Xcur":
        raise ValueError("not an Xcursor file")
    head, _, ntoc = struct.unpack_from("<III", data, 4)
    toc = [struct.unpack_from("<III", data, head + 12 * i) for i in range(ntoc)]
    images = [(nominal, pos) for kind, nominal, pos in toc if kind == 0xFFFD0002]
    if not images:
        raise ValueError("no images in Xcursor file")
    fit = min((n for n, _ in images if n >= size), default=max(n for n, _ in images))
    pos = next(p for n, p in images if n == fit)
    w, h, xhot, yhot = struct.unpack_from("<IIII", data, pos + 16)
    words = np.frombuffer(data, "<u4", w * h, pos + 36)
    return argb_image(words, w, h), (xhot, yhot), fit


def fallback_sprite(size):
    """Темы курсоров нет: простая стрелка, чтобы рендер всё равно работал."""
    k = 8  # рисуем крупно и уменьшаем, иначе края рваные
    s = size * k / 24
    pts = [(1, 1), (1, 18), (5.5, 14), (8.5, 21), (11.5, 19.5), (8.5, 13), (14, 13)]
    img = Image.new("RGBA", (size * k, size * k))
    ImageDraw.Draw(img).polygon([(x * s, y * s) for x, y in pts], fill=(0, 0, 0, 255), outline=(255, 255, 255, 255),
                                width=max(1, round(s)))
    return img.resize((size, size), Image.LANCZOS), (size / 24, size / 24)


def cursor_file_max(name):
    return 0  # только для экспорта в After Effects, а его на Linux нет


def cursor_sprite(name, size, custom=None):
    """Курсор как RGBA и его hotspot (x, y): системный — size×size по текущей теме; свой курсор программы из лога
    (custom: {имя: снимок из CursorLogger}) — снимок в масштабе size к системному курсору при записи."""
    if custom and name in custom:
        return cursorcore.custom_sprite(custom[name], size)
    theme = os.environ.get("XCURSOR_THEME") or _gsettings("cursor-theme") or "default"
    names, dirs = CURSORS.get(name, CURSORS["arrow"]), theme_dirs()
    path = find_cursor(names, theme, dirs) or find_cursor(names, "Adwaita", dirs) or find_cursor(names, "DMZ-White", dirs)
    if path:
        with contextlib.suppress(OSError, ValueError, struct.error):
            img, hot, nominal = read_xcursor(path.read_bytes(), size)
            k = size / nominal
            w, h = max(1, round(img.width * k)), max(1, round(img.height * k))
            return img.resize((w, h), Image.LANCZOS), (hot[0] * k, hot[1] * k)
    return fallback_sprite(size)
