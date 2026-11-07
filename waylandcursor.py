"""Курсор на Wayland: портал ScreenCast с cursor_mode = Metadata и PipeWire через ctypes.

Wayland не показывает программам чужой курсор. Единственный общий способ узнать его положение — попросить у системы
поток экрана: вместе с кадрами она отдаёт метаданные курсора (положение относительно экрана и картинку). Кадры нам не
нужны и сразу возвращаются. Система один раз спрашивает, какой экран отдать, дальше помнит (restore_token).

Клики в метаданных нет: они идут из /dev/input (linuxcursor.InputWatch).

ponytail: режим Metadata есть в порталах GNOME и KDE. У wlroots (Sway, Hyprland) его нет — там честная ошибка.
Для них нужен свой источник положения, например IPC Hyprland."""
import ctypes as C
import hashlib
import logging
import os
import secrets
import struct
import threading

from PIL import Image

from i18n import t

log = logging.getLogger("smooth")

# ---------- SPA POD: бинарный формат параметров PipeWire. В libspa это inline-функции, поэтому собираем сами ----------
T_ID, T_INT, T_RECT, T_FRACTION, T_OBJECT, T_CHOICE = 3, 4, 10, 11, 15, 19
CHOICE_RANGE, CHOICE_ENUM, CHOICE_FLAGS = 1, 3, 4
OBJ_FORMAT, OBJ_BUFFERS, OBJ_META = 0x40003, 0x40004, 0x40005
PARAM_ENUM_FORMAT, PARAM_FORMAT, PARAM_BUFFERS, PARAM_META = 3, 4, 5, 6
META_CURSOR = 5
CURSOR_FORMATS = {11: "RGBA", 12: "BGRA", 13: "ARGB", 14: "ABGR"}  # spa_video_format картинки курсора → порядок байт
CURSOR_HEAD = struct.Struct("<IIiiiiI")  # spa_meta_cursor: id, flags, position, hotspot, bitmap_offset
BITMAP_HEAD = struct.Struct("<IIIiI")    # spa_meta_bitmap: format, size, stride, offset


def pod(kind, body):
    return struct.pack("<II", len(body), kind) + body + bytes(-len(body) % 8)


def pod_id(v):
    return pod(T_ID, struct.pack("<I", v))


def pod_choice(mode, kind, values):
    """values — тела значений без заголовка, первое — значение по умолчанию."""
    return pod(T_CHOICE, struct.pack("<IIII", mode, 0, len(values[0]), kind) + b"".join(values))


def pod_object(kind, param, props):
    return pod(T_OBJECT, struct.pack("<II", kind, param) +
               b"".join(struct.pack("<II", key, 0) + value for key, value in props))


def pair(a, b):
    return struct.pack("<II", a, b)


def format_pod():
    """Какое видео принимаем: несжатое в памяти. Модификаторов нет, поэтому DMA-BUF не предложат."""
    return pod_object(OBJ_FORMAT, PARAM_ENUM_FORMAT, [
        (1, pod_id(2)),  # mediaType: video
        (2, pod_id(1)),  # mediaSubtype: raw
        (0x20001, pod_choice(CHOICE_ENUM, T_ID, [struct.pack("<I", f) for f in (8, 8, 7, 12, 11)])),
        (0x20003, pod_choice(CHOICE_RANGE, T_RECT, [pair(1920, 1080), pair(1, 1), pair(16384, 16384)])),
        (0x20004, pod_choice(CHOICE_RANGE, T_FRACTION, [pair(240, 1), pair(0, 1), pair(360, 1)])),
    ])


def cursor_meta_size(side):
    return CURSOR_HEAD.size + BITMAP_HEAD.size + side * side * 4


def stream_params():
    """Ответ на согласованный формат: нужны метаданные курсора и буферы в памяти."""
    ints = [struct.pack("<i", cursor_meta_size(s)) for s in (64, 1, 512)]
    return [pod_object(OBJ_META, PARAM_META, [(1, pod_id(META_CURSOR)), (2, pod_choice(CHOICE_RANGE, T_INT, ints))]),
            pod_object(OBJ_BUFFERS, PARAM_BUFFERS, [  # dataType: MemPtr | MemFd
                (6, pod_choice(CHOICE_FLAGS, T_INT, [struct.pack("<i", 1 << 1 | 1 << 2)]))])]


def parse_cursor(read):
    """Метаданные курсора. read(смещение, длина) → байты. Возвращает None, если курсора нет на этом экране,
    иначе (x, y, hx, hy, bitmap): bitmap — None, если картинка та же, иначе (Image или None, если курсор скрыт)."""
    cid, _, x, y, hx, hy, off = CURSOR_HEAD.unpack(read(0, CURSOR_HEAD.size))
    if not cid:
        return None
    if not off:
        return x, y, hx, hy, None
    fmt, w, h, stride, data = BITMAP_HEAD.unpack(read(off, BITMAP_HEAD.size))
    if not w or not h or fmt not in CURSOR_FORMATS:
        return x, y, hx, hy, (None,)
    img = Image.frombuffer("RGBA", (w, h), read(off + data, stride * h), "raw", CURSOR_FORMATS[fmt], stride, 1)
    return x, y, hx, hy, (img.copy(),)


# ---------- портал ----------
class Portal:
    """Сеанс org.freedesktop.portal.ScreenCast. В streams — поток экрана, в fd — соединение с PipeWire."""
    IFACE = "org.freedesktop.portal.ScreenCast"

    def __init__(self, token_path):
        from jeepney import DBusAddress, MessageType, new_method_call
        from jeepney.bus_messages import MatchRule, message_bus
        from jeepney.io.blocking import open_dbus_connection
        self.call_new, self.error_kind = new_method_call, MessageType.error
        self.MatchRule, self.message_bus = MatchRule, message_bus
        bus, path = "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop"
        self.addr = DBusAddress(path, bus_name=bus, interface=self.IFACE)
        self.props = DBusAddress(path, bus_name=bus, interface="org.freedesktop.DBus.Properties")
        self.session_addr = lambda p: DBusAddress(p, bus_name=bus, interface="org.freedesktop.portal.Session")
        self.conn = open_dbus_connection(bus="SESSION", enable_fds=True)
        self.sender = self.conn.unique_name[1:].replace(".", "_")
        self.session = None
        try:
            self._open(token_path)
        except Exception:
            self.close()
            raise

    def call(self, msg):
        reply = self.conn.send_and_get_reply(msg, timeout=30)
        if reply.header.message_type == self.error_kind:
            raise RuntimeError(t("Портал экрана: {}").format(reply.body[0] if reply.body else reply.header.fields))
        return reply

    def request(self, method, signature, *args, options):
        """Вызов портала: ответ приходит сигналом Response, когда пользователь закроет окно выбора."""
        token = "sc" + secrets.token_hex(8)
        path = f"/org/freedesktop/portal/desktop/request/{self.sender}/{token}"
        rule = self.MatchRule(type="signal", interface="org.freedesktop.portal.Request", member="Response", path=path)
        self.call(self.message_bus.AddMatch(rule))
        with self.conn.filter(rule) as queue:
            self.call(self.call_new(self.addr, method, signature, (*args, {**options, "handle_token": ("s", token)})))
            code, results = self.conn.recv_until_filtered(queue, timeout=300).body
        if code:
            raise RuntimeError(t("Экран для записи курсора не выбран"))
        return results

    def _open(self, token_path):
        modes = self.call(self.call_new(self.props, "Get", "ss", (self.IFACE, "AvailableCursorModes"))).body[0][1]
        if not modes & 4:
            raise RuntimeError(t("Этот рабочий стол не отдаёт положение курсора (нужны GNOME или KDE на Wayland). "
                                 "Войдите в сеанс X11"))
        res = self.request("CreateSession", "a{sv}", options={"session_handle_token": ("s", "sc" + secrets.token_hex(8))})
        self.session = res["session_handle"][1]
        opts = {"types": ("u", 1), "multiple": ("b", False), "cursor_mode": ("u", 4), "persist_mode": ("u", 2)}
        if token_path.exists():
            opts["restore_token"] = ("s", token_path.read_text(encoding="utf-8").strip())
        self.request("SelectSources", "oa{sv}", self.session, options=opts)
        res = self.request("Start", "osa{sv}", self.session, "", options={})
        if "restore_token" in res:  # меняется при каждом запуске
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(res["restore_token"][1], encoding="utf-8")
        self.node, info = res["streams"][1][0]
        self.size = info.get("size", (None, (0, 0)))[1]
        self.fd = self.call(self.call_new(self.addr, "OpenPipeWireRemote", "oa{sv}", (self.session, {}))).body[0].to_raw_fd()

    def close(self):
        if self.session:
            self.conn.send(self.call_new(self.session_addr(self.session), "Close", None, ()))
        self.conn.close()


# ---------- PipeWire ----------
class StreamEvents(C.Structure):  # struct pw_stream_events, версия 2
    _fields_ = [("version", C.c_uint32)] + [(n, C.c_void_p) for n in (
        "destroy", "state_changed", "control_info", "io_changed", "param_changed", "add_buffer", "remove_buffer",
        "process", "drained", "command", "trigger_done")]


class SpaMeta(C.Structure):
    _fields_ = [("type", C.c_uint32), ("size", C.c_uint32), ("data", C.c_void_p)]


class SpaBuffer(C.Structure):
    _fields_ = [("n_metas", C.c_uint32), ("n_datas", C.c_uint32), ("metas", C.POINTER(SpaMeta)), ("datas", C.c_void_p)]


class PwBuffer(C.Structure):
    _fields_ = [("buffer", C.POINTER(SpaBuffer)), ("user_data", C.c_void_p), ("size", C.c_uint64)]


def pipewire():
    try:
        pw = C.CDLL("libpipewire-0.3.so.0")
    except OSError as e:
        raise RuntimeError(t("Нет библиотеки PipeWire ({}). Поставьте libpipewire-0.3-0").format(e))
    v = C.c_void_p
    for name, args, res in (
            ("pw_init", [v, v], None), ("pw_thread_loop_new", [C.c_char_p, v], v), ("pw_thread_loop_get_loop", [v], v),
            ("pw_thread_loop_start", [v], C.c_int), ("pw_thread_loop_stop", [v], None),
            ("pw_thread_loop_lock", [v], None), ("pw_thread_loop_unlock", [v], None),
            ("pw_thread_loop_destroy", [v], None), ("pw_context_new", [v, v, C.c_size_t], v),
            ("pw_context_destroy", [v], None), ("pw_context_connect_fd", [v, C.c_int, v, C.c_size_t], v),
            ("pw_core_disconnect", [v], C.c_int), ("pw_stream_new", [v, C.c_char_p, v], v),
            ("pw_stream_add_listener", [v, v, v, v], None), ("pw_stream_destroy", [v], None),
            ("pw_stream_connect", [v, C.c_int, C.c_uint32, C.c_int, v, C.c_uint32], C.c_int),
            ("pw_stream_update_params", [v, v, C.c_uint32], C.c_int), ("pw_stream_dequeue_buffer", [v], v),
            ("pw_stream_queue_buffer", [v, v], C.c_int)):
        f = getattr(pw, name)
        f.argtypes, f.restype = args, res
    pw.pw_properties_new.restype = v  # с переменным числом аргументов: argtypes не задаём
    pw.pw_init(None, None)
    return pw


def pod_array(pods):
    """Массив указателей на POD и то, что должно жить, пока он нужен."""
    bufs = [C.create_string_buffer(p, len(p)) for p in pods]
    return (C.c_void_p * len(bufs))(*[C.addressof(b) for b in bufs]), bufs


class CursorStream:
    """Поток экрана node на соединении fd. on_cursor(разбор parse_cursor или None) зовётся из потока PipeWire."""

    def __init__(self, fd, node, on_cursor, on_error):
        self.pw = pw = pipewire()
        self.on_cursor, self.on_error = on_cursor, on_error
        self.loop = pw.pw_thread_loop_new(b"smooth-cursor", None)
        self.context = pw.pw_context_new(pw.pw_thread_loop_get_loop(self.loop), None, 0)
        pw.pw_thread_loop_start(self.loop)
        pw.pw_thread_loop_lock(self.loop)
        try:
            self.core = pw.pw_context_connect_fd(self.context, fd, None, 0)
            if not self.core:
                raise RuntimeError(t("Не удалось подключиться к PipeWire"))
            props = pw.pw_properties_new(b"media.type", b"Video", b"media.category", b"Capture", b"media.role", b"Screen",
                                         None)
            self.stream = pw.pw_stream_new(self.core, b"Smooth Cursor", props)
            # ссылки на обратные вызовы и hook живут в объекте: PipeWire хранит только указатели
            self.cb = (C.CFUNCTYPE(None, C.c_void_p, C.c_int, C.c_int, C.c_char_p)(self._state),
                       C.CFUNCTYPE(None, C.c_void_p, C.c_uint32, C.c_void_p)(self._param),
                       C.CFUNCTYPE(None, C.c_void_p)(self._process))
            self.events = StreamEvents(version=2, **{n: C.cast(f, C.c_void_p).value for n, f in zip(
                ("state_changed", "param_changed", "process"), self.cb)})
            self.hook = C.create_string_buffer(128)  # struct spa_hook, заполняет PipeWire
            pw.pw_stream_add_listener(self.stream, self.hook, C.byref(self.events), None)
            self.params, self.keep = pod_array(stream_params())
            formats, keep = pod_array([format_pod()])
            # INPUT; AUTOCONNECT | MAP_BUFFERS
            if pw.pw_stream_connect(self.stream, 0, node, 1 << 0 | 1 << 2, formats, 1) < 0:
                raise RuntimeError(t("Не удалось подключиться к PipeWire"))
            del keep
        finally:
            pw.pw_thread_loop_unlock(self.loop)

    def _state(self, _, old, state, error):
        if state == -1:
            self.on_error(error.decode("utf-8", "replace") if error else "PipeWire")

    def _param(self, _, param_id, param):
        if param_id == PARAM_FORMAT and param:
            self.pw.pw_stream_update_params(self.stream, self.params, len(self.keep))

    def _process(self, _):
        last = None
        while b := self.pw.pw_stream_dequeue_buffer(self.stream):  # отстали — нужен только свежий
            if last:
                self.pw.pw_stream_queue_buffer(self.stream, last)
            last = b
        if not last:
            return
        sb = C.cast(last, C.POINTER(PwBuffer)).contents.buffer.contents
        for i in range(sb.n_metas):
            m = sb.metas[i]
            if m.type == META_CURSOR and m.size >= CURSOR_HEAD.size:
                self.on_cursor(parse_cursor(lambda off, n, base=m.data: C.string_at(base + off, n)))
        self.pw.pw_stream_queue_buffer(self.stream, last)

    def close(self):
        pw = self.pw
        pw.pw_thread_loop_stop(self.loop)
        pw.pw_stream_destroy(self.stream)
        pw.pw_core_disconnect(self.core)
        pw.pw_context_destroy(self.context)
        pw.pw_thread_loop_destroy(self.loop)


class WaylandSource:
    """read() — как у X11Source: (x, y, вид, виден ли, левая кнопка, правая кнопка)."""

    def __init__(self, display):
        import linuxcursor
        import settings
        self.buttons = None
        try:
            self.buttons = linuxcursor.input_watch().down
        except RuntimeError as e:
            log.warning("%s", e)  # без кликов курсор всё равно плавный: нет только анимации нажатия
        self.state = (0, 0, None, False)  # x, y, вид, виден ли: заменяется целиком, поток PipeWire пишет, логгер читает
        self.shapes, self.shape, self.error = {}, None, None
        self.portal = Portal(settings.PATH.with_name("wayland_screen_token"))
        w, h = self.portal.size
        if (w, h) != (0, 0) and (w, h) != (display["width"], display["height"]):
            log.warning(t("Для курсора выбран экран %dx%d, а OBS снимает %dx%d — выберите тот же экран"),
                        w, h, display["width"], display["height"])
        try:
            self.stream = CursorStream(self.portal.fd, self.portal.node, self._cursor, self._failed)
        except Exception:
            self.portal.close()
            raise

    def _failed(self, message):
        self.error = message
        log.error(t("Поток экрана прервался: %s"), message)

    def _cursor(self, parsed):
        x, y, shape, _ = self.state
        if parsed is None:  # курсор ушёл на другой экран
            self.state = (x, y, shape, False)
            return
        x, y, hx, hy, bitmap = parsed
        if bitmap:
            img = bitmap[0]
            if img is None or not img.getchannel("A").getbbox():
                self.shape = None  # скрытый курсор
            else:
                self.shape = hashlib.sha1(img.tobytes() + repr((hx, hy, img.size)).encode()).hexdigest()
                self.shapes.setdefault(self.shape, (None, (img, (hx, hy))))
        self.state = (x, y, self.shape, self.shape is not None)

    def read(self):
        x, y, shape, showing = self.state
        down = self.buttons or ()
        return x, y, shape, showing, 0x110 in down, 0x111 in down  # BTN_LEFT, BTN_RIGHT

    def describe(self, shape):
        return self.shapes.get(shape, (None, None))

    def close(self):
        self.stream.close()
        self.portal.close()
        if hasattr(self.portal, "fd"):
            try:
                os.close(self.portal.fd)
            except OSError:
                pass  # PipeWire закрывает соединение сам
