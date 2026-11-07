"""Win32 через ctypes: DPI awareness, поиск дисплея, логгер курсора, спрайты системных курсоров."""
import contextlib
import ctypes as C
import ctypes.wintypes as W
import os
import time
import winreg

import numpy as np
from PIL import Image

import cursorcore
from autostart import running_exe  # noqa: F401 — wc.running_exe зовут ae.py и premiere.py
from cursorcore import corner_pos  # noqa: F401 — wc.corner_pos зовёт app.pyw

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
    return cursorcore.primary(mons or list_monitors())


def find_display(select="auto", obs_ids=()):
    return cursorcore.find_display(list_monitors(), select, obs_ids)


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


class CursorLogger(cursorcore.BaseLogger):
    def __init__(self, display, hz=240):
        super().__init__(display, hz, TYPES, cursor_base_size() * display["dpi"] / 96)

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
            nxt = self.wait_tick(nxt)

    def _capture(self, h, seen):
        """Свой курсор программы (инструменты Premiere и т. п.): картинка в родном размере уходит в лог."""
        return self.add_custom(snapshot(h, self.ref), seen)


MONITOR_KINDS = ("monitor_capture",)  # какие источники OBS захватывают экран


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
    pt, ci = W.POINT(), CURSORINFO(cbSize=C.sizeof(CURSORINFO))
    with physical():
        user32.GetCursorPos(C.byref(pt))
        if not user32.GetCursorInfo(C.byref(ci)):
            ci.hCursor = None
    return pt.x, pt.y, ci.hCursor, user32.GetAsyncKeyState(1) < 0, ci.flags & 1


def system_shapes():
    """Курсор из pointer() → имя системного вида."""
    return {user32.LoadCursorW(None, cid): n for n, (cid, _) in CURSORS.items()}


def work_area():
    """Рабочая область основного экрана без панели задач: (left, top, right, bottom)."""
    r = W.RECT()
    user32.SystemParametersInfoW(0x30, 0, C.byref(r), 0)  # SPI_GETWORKAREA
    return r.left, r.top, r.right, r.bottom


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
        return cursorcore.custom_sprite(custom[name], size)
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
    return cursorcore.pack_snapshot(sp, ref) if sp else None


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
