"""Win32 через ctypes: DPI awareness, поиск дисплея, логгер курсора, спрайты системных курсоров."""
import contextlib
import ctypes as C
import ctypes.wintypes as W
import os
import re
import threading
import time
import winreg

import numpy as np
from PIL import Image

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


def list_monitors():
    out = []

    def cb(hmon, hdc, rect, lparam):
        mi = MONITORINFOEXW(cbSize=C.sizeof(MONITORINFOEXW))
        user32.GetMonitorInfoW(hmon, C.byref(mi))
        dpi, _ = C.c_uint(), C.c_uint()
        shcore.GetDpiForMonitor(hmon, 0, C.byref(dpi), C.byref(_))
        dd = DISPLAY_DEVICEW(cb=C.sizeof(DISPLAY_DEVICEW))
        user32.EnumDisplayDevicesW(mi.szDevice, 0, C.byref(dd), 1)  # EDD_GET_DEVICE_INTERFACE_NAME: id как у OBS
        r = mi.rcMonitor
        out.append({"device": mi.szDevice, "id": dd.DeviceID, "left": r.left, "top": r.top,
                    "width": r.right - r.left, "height": r.bottom - r.top, "dpi": dpi.value})
        return True

    proc = C.WINFUNCTYPE(W.BOOL, W.HMONITOR, W.HDC, C.POINTER(W.RECT), W.LPARAM)(cb)
    with physical():
        user32.EnumDisplayMonitors(None, None, proc, 0)
    return out


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


class CursorLogger:
    """Опрашивает курсор в отдельном потоке. Время — perf_counter_ns, координаты — относительно дисплея."""

    def __init__(self, display, hz=240):
        self.display, self.period = display, 1e9 / hz
        self.samples = []  # (t_ns, x, y, type_idx, showing)
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
        ci, pt = CURSORINFO(cbSize=C.sizeof(CURSORINFO)), W.POINT()
        left, top = self.display["left"], self.display["top"]
        buttons, prev = {"L": 0x01, "R": 0x02}, {"L": False, "R": False}  # VK_LBUTTON, VK_RBUTTON
        nxt = time.perf_counter_ns()
        while not self._stop.is_set():
            t = time.perf_counter_ns()
            user32.GetCursorPos(C.byref(pt))
            user32.GetCursorInfo(C.byref(ci))
            x, y = pt.x - left, pt.y - top
            self.samples.append((t, x, y, handles.get(ci.hCursor, 0), ci.flags & 1))  # CURSOR_SHOWING
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
        return {"version": 1, "display": self.display, "hz": round(1e9 / self.period), "types": TYPES,
                "samples": samples, "clicks": clicks}


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


def cursor_sprite(name, size):
    """Текущий системный курсор как RGBA size×size и его hotspot (x, y)."""
    cid, reg = CURSORS.get(name, CURSORS["arrow"])
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Cursors") as k:
            path = os.path.expandvars(winreg.QueryValueEx(k, reg)[0])
    except OSError:
        path = ""
    h = user32.LoadImageW(None, path, 2, size, size, 0x10) if path else None  # IMAGE_CURSOR, LR_LOADFROMFILE
    shared = not h
    if shared:  # схема без файла — встроенный курсор
        h = user32.LoadImageW(None, cid, 2, 0, 0, 0x8000)  # LR_SHARED
    ii, bm = ICONINFO(), BITMAP()
    user32.GetIconInfo(h, C.byref(ii))
    gdi32.GetObjectW(ii.hbmMask, C.sizeof(BITMAP), C.byref(bm))
    hot = (ii.xHotspot * size / bm.bmWidth, ii.yHotspot * size / bm.bmWidth)
    for b in (ii.hbmMask, ii.hbmColor):
        if b:
            gdi32.DeleteObject(b)
    black, white = _draw(h, size, 0), _draw(h, size, 255)
    if not shared:
        user32.DestroyCursor(h)

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
