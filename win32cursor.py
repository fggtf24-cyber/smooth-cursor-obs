"""Win32 через ctypes: DPI awareness, поиск дисплея, логгер курсора, спрайты системных курсоров."""
import base64
import contextlib
import ctypes as C
import ctypes.wintypes as W
import io
import os
import re
import threading
import time
import winreg

import numpy as np
from PIL import Image

from autostart import running_exe  # noqa: F401 — wc.running_exe зовут ae.py и premiere.py
from i18n import t

user32 = C.WinDLL("user32", use_last_error=True)
gdi32 = C.WinDLL("gdi32")
shcore = C.WinDLL("shcore")

# Per-Monitor v2 до любых вызовов с координатами, иначе при масштабе != 100% GetCursorPos виртуализируется.
_PMV2 = C.c_void_p(-4)
user32.SetProcessDpiAwarenessContext.argtypes = [C.c_void_p]
user32.GetThreadDpiAwarenessContext.restype = C.c_void_p
user32.AreDpiAwarenessContextsEqual.argtypes = [C.c_void_p, C.c_void_p]
user32.SetProcessDpiAwarenessContext(_PMV2)
if not user32.AreDpiAwarenessContextsEqual(user32.GetThreadDpiAwarenessContext(), _PMV2):
    raise RuntimeError("Не удалось включить Per-Monitor DPI Aware v2")
user32.SetThreadDpiAwarenessContext.argtypes = [C.c_void_p]
user32.SetThreadDpiAwarenessContext.restype = C.c_void_p


@contextlib.contextmanager
def physical():
    """Настоящие пиксели и в потоке окна программы: его масштабирует Windows (app.pyw), и без этого координаты
    курсора и размеры мониторов там пересчитаны под масштаб экрана."""
    old = user32.SetThreadDpiAwarenessContext(_PMV2)
    try:
        yield
    finally:
        if old:
            user32.SetThreadDpiAwarenessContext(old)

user32.LoadCursorW.argtypes = [W.HINSTANCE, C.c_void_p]
user32.LoadCursorW.restype = C.c_void_p
user32.LoadImageW.argtypes = [W.HINSTANCE, C.c_void_p, W.UINT, C.c_int, C.c_int, W.UINT]
user32.LoadImageW.restype = C.c_void_p
user32.GetAsyncKeyState.restype = C.c_short
user32.GetIconInfo.argtypes = [C.c_void_p, C.c_void_p]
user32.DrawIconEx.argtypes = [W.HDC, C.c_int, C.c_int, C.c_void_p, C.c_int, C.c_int, W.UINT, W.HBRUSH, W.UINT]
user32.DestroyCursor.argtypes = [C.c_void_p]
gdi32.CreateCompatibleDC.argtypes = [W.HDC]
gdi32.CreateCompatibleDC.restype = W.HDC
gdi32.DeleteDC.argtypes = [W.HDC]
gdi32.CreateDIBSection.restype = W.HBITMAP
gdi32.CreateDIBSection.argtypes = [W.HDC, C.c_void_p, W.UINT, C.POINTER(C.c_void_p), W.HANDLE, W.DWORD]
gdi32.SelectObject.argtypes = [W.HDC, W.HGDIOBJ]
gdi32.SelectObject.restype = W.HGDIOBJ
gdi32.DeleteObject.argtypes = [W.HGDIOBJ]
gdi32.GetObjectW.argtypes = [W.HGDIOBJ, C.c_int, C.c_void_p]


class CURSORINFO(C.Structure):
    _fields_ = [("cbSize", W.DWORD), ("flags", W.DWORD), ("hCursor", C.c_void_p), ("ptScreenPos", W.POINT)]


class MONITORINFOEXW(C.Structure):
    _fields_ = [("cbSize", W.DWORD), ("rcMonitor", W.RECT), ("rcWork", W.RECT), ("dwFlags", W.DWORD),
                ("szDevice", W.WCHAR * 32)]


class DISPLAY_DEVICEW(C.Structure):
    _fields_ = [("cb", W.DWORD), ("DeviceName", W.WCHAR * 32), ("DeviceString", W.WCHAR * 128),
                ("StateFlags", W.DWORD), ("DeviceID", W.WCHAR * 128), ("DeviceKey", W.WCHAR * 128)]


class ICONINFO(C.Structure):
    _fields_ = [("fIcon", W.BOOL), ("xHotspot", W.DWORD), ("yHotspot", W.DWORD),
                ("hbmMask", W.HBITMAP), ("hbmColor", W.HBITMAP)]


class BITMAPINFOHEADER(C.Structure):
    _fields_ = [("biSize", W.DWORD), ("biWidth", W.LONG), ("biHeight", W.LONG), ("biPlanes", W.WORD),
                ("biBitCount", W.WORD), ("biCompression", W.DWORD), ("biSizeImage", W.DWORD),
                ("biXPelsPerMeter", W.LONG), ("biYPelsPerMeter", W.LONG), ("biClrUsed", W.DWORD),
                ("biClrImportant", W.DWORD)]


class BITMAP(C.Structure):
    _fields_ = [("bmType", W.LONG), ("bmWidth", W.LONG), ("bmHeight", W.LONG), ("bmWidthBytes", W.LONG),
                ("bmPlanes", W.WORD), ("bmBitsPixel", W.WORD), ("bmBits", C.c_void_p)]


# Системные курсоры: имя -> (OCR/IDC id, имя значения в HKCU\Control Panel\Cursors). Индекс 0 (arrow) — фолбэк.
CURSORS = {
    "arrow": (32512, "Arrow"), "ibeam": (32513, "IBeam"), "wait": (32514, "Wait"), "cross": (32515, "Crosshair"),
    "size_nwse": (32642, "SizeNWSE"), "size_nesw": (32643, "SizeNESW"), "size_we": (32644, "SizeWE"),
    "size_ns": (32645, "SizeNS"), "size_all": (32646, "SizeAll"), "no": (32648, "No"), "hand": (32649, "Hand"),
    "busy": (32650, "AppStarting"), "help": (32651, "Help"),
}
TYPES = list(CURSORS)


def virtual_devices():
    """Имена экранов виртуального адаптера Virtual Display Driver."""
    out, i, dd = set(), 0, DISPLAY_DEVICEW(cb=C.sizeof(DISPLAY_DEVICEW))
    while user32.EnumDisplayDevicesW(None, i, C.byref(dd), 0):
        if dd.DeviceString == "Virtual Display Driver":
            out.add(dd.DeviceName)
        i += 1
    return out


def list_monitors():
    out, virtual = [], virtual_devices()

    def cb(hmon, hdc, rect, lparam):
        mi = MONITORINFOEXW(cbSize=C.sizeof(MONITORINFOEXW))
        user32.GetMonitorInfoW(hmon, C.byref(mi))
        dpi, _ = C.c_uint(), C.c_uint()
        shcore.GetDpiForMonitor(hmon, 0, C.byref(dpi), C.byref(_))
        dd = DISPLAY_DEVICEW(cb=C.sizeof(DISPLAY_DEVICEW))
        user32.EnumDisplayDevicesW(mi.szDevice, 0, C.byref(dd), 1)  # EDD_GET_DEVICE_INTERFACE_NAME: id как у OBS
        r, a = mi.rcMonitor, mi.rcWork
        out.append({"device": mi.szDevice, "id": dd.DeviceID, "left": r.left, "top": r.top,
                    "work": [a.left, a.top, a.right, a.bottom],
                    "width": r.right - r.left, "height": r.bottom - r.top, "dpi": dpi.value,
                    "virtual": mi.szDevice in virtual})
        return True

    proc = C.WINFUNCTYPE(W.BOOL, W.HMONITOR, W.HDC, C.POINTER(W.RECT), W.LPARAM)(cb)
    with physical():
        user32.EnumDisplayMonitors(None, None, proc, 0)
    return out


def primary(mons=None):
    """Основной монитор: у него левый верхний угол в (0, 0)."""
    mons = mons or list_monitors()
    return next((m for m in mons if (m["left"], m["top"]) == (0, 0)), mons[0])


def corner_pos(mon, w, h):
    """Левый верх окна w×h в правом нижнем углу рабочей области монитора. Всё от самого экрана: отступы — под его
    масштаб, панель задач с любой стороны рабочая область уже учла. Если она занимает экран до низа, панель скрывается
    или снизу лежит док поверх экрана: поднимаем на его высоту."""
    k = mon["dpi"] / 96
    gap = round(16 * k)
    _, _, right, bottom = mon["work"]
    if bottom >= mon["top"] + mon["height"]:
        bottom -= round(56 * k)
    return right - w - gap, bottom - h - gap


def find_display(select="auto", obs_ids=()):
    """select: "auto" — монитор из obs_ids (захваты экрана в сцене OBS); "5120x2160" — по разрешению;
    иначе подстрока имени/ID монитора ("DISPLAY2", "MTT1337")."""
    mons = list_monitors()
    s = str(select).strip().lower()
    if s == "auto":
        hits = [m for m in mons if m["id"] in obs_ids]
        why = t("в текущей сцене OBS нет включённого «Захвата экрана» подключённого монитора" if not hits else
                "в текущей сцене OBS несколько «Захватов экрана» — оставьте один или задайте display в config.yaml")
    elif re.fullmatch(r"\d+x\d+", s):
        hits = [m for m in mons if f"{m['width']}x{m['height']}" == s]
        why = t("монитор {} не найден" if not hits else "мониторов {} несколько — задайте display по имени").format(s)
    else:
        hits = [m for m in mons if s in (m["device"] + m["id"]).lower()]
        why = t("монитор «{}» не найден" if not hits else "под «{}» подходит несколько мониторов").format(select)
    if len(hits) == 1:
        return hits[0]
    found = "\n".join(f"  {m['device']} {m['width']}x{m['height']} @({m['left']},{m['top']}) dpi {m['dpi']} {m['id']}"
                      for m in mons)
    raise LookupError(t("Не могу выбрать дисплей: {}. Мониторы:\n{}").format(why, found))


def find_window(part):
    """Видимое окно, в заголовке которого есть part; иначе None."""
    hits, buf = [], C.create_unicode_buffer(512)

    def cb(h, _):
        if user32.IsWindowVisible(h) and user32.GetWindowTextW(h, buf, 512) and part in buf.value:
            hits.append(h)
            return False
        return True

    user32.EnumWindows(C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)(cb), 0)
    return hits[0] if hits else None


class CursorLogger:
    """Опрашивает курсор в отдельном потоке. Время — perf_counter_ns, координаты — относительно дисплея."""

    def __init__(self, display, hz=240):
        self.display, self.period = display, 1e9 / hz
        self.samples = []  # (t_ns, x, y, type_idx, showing)
        self.types, self.custom = list(TYPES), {}  # + свои курсоры программ: имя → снимок (см. _capture)
        self.ref = cursor_base_size() * display["dpi"] / 96  # размер системного курсора на экране при записи
        self.clicks = []   # (t_ns, "L"/"R", down 1/0, x, y)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join()

    def _run(self):
        handles = {user32.LoadCursorW(None, cid): i for i, (cid, _) in enumerate(CURSORS.values())}
        ci, pt, seen = CURSORINFO(cbSize=C.sizeof(CURSORINFO)), W.POINT(), {}
        left, top = self.display["left"], self.display["top"]
        buttons, prev = {"L": 0x01, "R": 0x02}, {"L": False, "R": False}  # VK_LBUTTON, VK_RBUTTON
        nxt = time.perf_counter_ns()
        while not self._stop.is_set():
            t = time.perf_counter_ns()
            user32.GetCursorPos(C.byref(pt))
            ci.cbSize = C.sizeof(CURSORINFO)  # Windows 11 обнуляет cbSize после вызова, и следующий падает
            if not user32.GetCursorInfo(C.byref(ci)):
                ci.hCursor, ci.flags = None, 1  # не знаем, что за курсор: стрелка, но видимая
            x, y = pt.x - left, pt.y - top
            k = handles.get(ci.hCursor)
            if k is None:  # не системный курсор — снимаем картинку один раз на хэндл
                k = handles[ci.hCursor] = self._capture(ci.hCursor, seen)
            self.samples.append((t, x, y, k, ci.flags & 1))  # CURSOR_SHOWING
            for b, vk in buttons.items():
                down = user32.GetAsyncKeyState(vk) < 0
                if down != prev[b]:
                    self.clicks.append((t, b, int(down), x, y))
                    prev[b] = down
            nxt += self.period
            delay = nxt - time.perf_counter_ns()
            if delay > 0:
                time.sleep(delay / 1e9)
            else:
                nxt = time.perf_counter_ns()  # отстали — не догоняем пачкой

    def _capture(self, h, seen):
        """Свой курсор программы (инструменты Premiere и т. п.): картинка в родном размере уходит в лог,
        одинаковые картинки с разными хэндлами — один тип. 0 (стрелка), если снять не вышло."""
        snap = snapshot(h, self.ref)
        if not snap:
            return 0
        key = snap["png"] + repr(snap["hot"])
        if key not in seen:
            name = f"custom{len(self.custom)}"
            self.custom[name] = snap
            self.types.append(name)
            seen[key] = len(self.types) - 1
        return seen[key]

    def to_dict(self, t0_ns, pauses=(), end_ns=None):
        """Лог для файла записи, начатого в t0_ns (и законченного в end_ns, если OBS разбил запись на файлы).
        pauses — [[начало, конец или None]] пауз записи, ns: на видео их нет, поэтому сэмплы из пауз выкидываем,
        а время после паузы сдвигаем на её длину."""
        pauses = [(a, b) for a, b in pauses if a >= t0_ns and (end_ns is None or a < end_ns)]

        def at(t):
            """(мс на видео, попал ли момент в паузу); момент внутри паузы — её начало, то есть склейка."""
            shift = 0
            for a, b in pauses:
                if t < a:
                    break
                if b is None or t < b:
                    return round((a - t0_ns - shift) / 1e6, 3), True
                shift += b - a
            return round((t - t0_ns - shift) / 1e6, 3), False

        lo, hi = t0_ns - 1e9, (end_ns or float("inf")) + 1e9  # с запасом в секунду по краям файла
        samples, clicks = [], []
        for t, x, y, k, v in self.samples:
            if lo <= t <= hi and not (m := at(t))[1]:
                samples.append([m[0], x, y, k, v])
        for t, b, d, x, y in self.clicks:
            if lo <= t <= hi and not ((m := at(t))[1] and d):  # нажатие в паузе на видео не попало, отпускание — на склейке
                clicks.append([m[0], b, d, x, y])
        return {"version": 1, "display": self.display, "hz": round(1e9 / self.period), "types": self.types,
                "custom": self.custom, "samples": samples, "clicks": clicks}


def cursor_base_size():
    """Базовый размер курсора при 100% (меняется в «Специальные возможности → Указатель мыши»)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Cursors") as k:
            return winreg.QueryValueEx(k, "CursorBaseSize")[0]
    except OSError:
        return 32


def _draw(h, size, bg):
    dc = gdi32.CreateCompatibleDC(None)
    bits = C.c_void_p()
    bmi = BITMAPINFOHEADER(biSize=C.sizeof(BITMAPINFOHEADER), biWidth=size, biHeight=-size, biPlanes=1, biBitCount=32)
    bmp = gdi32.CreateDIBSection(dc, C.byref(bmi), 0, C.byref(bits), None, 0)
    old = gdi32.SelectObject(dc, bmp)
    C.memset(bits, bg, size * size * 4)
    user32.DrawIconEx(dc, 0, 0, h, size, size, 0, None, 3)  # DI_NORMAL
    gdi32.GdiFlush()
    px = np.frombuffer(C.string_at(bits, size * size * 4), np.uint8).reshape(size, size, 4)[..., 2::-1].astype(float)
    gdi32.SelectObject(dc, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(dc)
    return px


def _cursor_file(reg):
    """Файл курсора из схемы Windows; "" — схема без файла (встроенный курсор)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Cursors") as k:
            return os.path.expandvars(winreg.QueryValueEx(k, reg)[0])
    except OSError:
        return ""


def cursor_file_max(name):
    """Самая крупная картинка в файле курсора .cur, px: крупнее Windows только растягивает. 0 — не .cur."""
    try:
        with open(_cursor_file(CURSORS.get(name, CURSORS["arrow"])[1]), "rb") as f:
            head = f.read(6 + 16 * 64)  # заголовок и каталог картинок: ширина — первый байт записи, 0 — это 256
        if head[2:4] != b"\x02\x00":
            return 0
        return max((head[6 + 16 * i] or 256) for i in range(min(int.from_bytes(head[4:6], "little"), 64)))
    except (OSError, ValueError):
        return 0


def cursor_sprite(name, size, custom=None):
    """Курсор как RGBA и его hotspot (x, y): системный — size×size по текущей схеме Windows; свой курсор программы
    из лога (custom: {имя: снимок из CursorLogger}) — снимок в масштабе size к системному курсору при записи."""
    if custom and name in custom:
        c = custom[name]
        img = Image.open(io.BytesIO(base64.b64decode(c["png"]))).convert("RGBA")
        k = size / c["ref"]
        w, h = max(1, round(img.width * k)), max(1, round(img.height * k))
        return img.resize((w, h), Image.LANCZOS), (c["hot"][0] * w / img.width, c["hot"][1] * h / img.height)
    cid, reg = CURSORS.get(name, CURSORS["arrow"])
    path = _cursor_file(reg)
    h = user32.LoadImageW(None, path, 2, size, size, 0x10) if path else None  # IMAGE_CURSOR, LR_LOADFROMFILE
    shared = not h
    if shared:  # схема без файла — встроенный курсор
        h = user32.LoadImageW(None, cid, 2, 0, 0, 0x8000)  # LR_SHARED
    out = handle_sprite(h, size)
    if not shared:
        user32.DestroyCursor(h)
    return out


def snapshot(h, ref):
    """Свой курсор программы по HCURSOR → {png base64 в родном размере, hot, ref} для cursor_sprite; None — не вышло.
    ref — размер системного курсора на экране в тот момент, px."""
    sp = handle_sprite(h) if h else None
    if not sp:
        return None
    buf = io.BytesIO()
    sp[0].save(buf, "PNG")
    return {"png": base64.b64encode(buf.getvalue()).decode(), "hot": sp[1], "ref": ref}


def handle_sprite(h, size=0):
    """Курсор по HCURSOR (в т. ч. чужой программы) как RGBA size×size и hotspot; size=0 — родной размер."""
    ii, bm = ICONINFO(), BITMAP()
    if not user32.GetIconInfo(h, C.byref(ii)):
        return None
    gdi32.GetObjectW(ii.hbmMask, C.sizeof(BITMAP), C.byref(bm))
    size = size or bm.bmWidth
    hot = (ii.xHotspot * size / bm.bmWidth, ii.yHotspot * size / bm.bmWidth)
    for b in (ii.hbmMask, ii.hbmColor):
        if b:
            gdi32.DeleteObject(b)
    black, white = _draw(h, size, 0), _draw(h, size, 255)

    diff = (white - black).mean(-1)  # = 255·(1−α); у инвертирующих пикселей отрицательно
    a = np.clip(1 - diff / 255, 0, 1)
    rgb = black / np.maximum(a, 1e-6)[..., None]
    inv = diff < -128
    if inv.any():  # монохромный XOR-курсор (классический I-beam) инвертирует фон — рисуем чёрным с белой обводкой
        near = np.zeros_like(inv)
        pad = np.pad(inv, 1)
        for dy in range(3):
            for dx in range(3):
                near |= pad[dy:dy + size, dx:dx + size]
        ring = near & ~inv & (a == 0)
        a[inv], rgb[inv] = 1, 0
        a[ring], rgb[ring] = 1, 255
    img = np.dstack([np.clip(rgb, 0, 255), a * 255]).round().astype(np.uint8)
    return Image.fromarray(img, "RGBA"), hot


# ---------- то, что окно программы и OBS зовут одинаково на Windows и Linux (linuxcursor.py) ----------
MONITOR_KINDS = ("monitor_capture",)  # какие источники OBS захватывают экран
_pt, _ci = W.POINT(), CURSORINFO(cbSize=C.sizeof(CURSORINFO))


def capture_id(kind, settings, transform):
    """Какой монитор снимает источник OBS: id как в list_monitors."""
    return settings.get("monitor_id")


def adjust_display(d, transform):
    """На Windows размеры экрана и источника совпадают."""


def capture_input(d, cursor, kinds):
    """(вид источника OBS, настройки) для захвата экрана d."""
    return "monitor_capture", {"monitor_id": d["id"], "capture_cursor": cursor, "method": 0}


def pointer():
    """(x, y, курсор, нажата ли левая кнопка, виден ли) сейчас, в настоящих пикселях. Курсор понимает snapshot."""
    with physical():
        user32.GetCursorPos(C.byref(_pt))
        _ci.cbSize = C.sizeof(CURSORINFO)  # Windows 11 обнуляет cbSize после вызова, и следующий падает
        if not user32.GetCursorInfo(C.byref(_ci)):
            _ci.hCursor = None
    return _pt.x, _pt.y, _ci.hCursor, user32.GetAsyncKeyState(1) < 0, _ci.flags & 1


def system_shapes():
    """Курсор из pointer() → имя системного вида."""
    return {user32.LoadCursorW(None, cid): n for n, (cid, _) in CURSORS.items()}


def work_area():
    """Рабочая область основного экрана без панели задач: (left, top, right, bottom)."""
    r = W.RECT()
    user32.SystemParametersInfoW(0x30, 0, C.byref(r), 0)  # SPI_GETWORKAREA
    return r.left, r.top, r.right, r.bottom
