"""Проверка на настоящих серверах, только в CI (.github/workflows/build.yml):

    xvfb-run -a python linux/ci_check.py x11
    dbus-run-session -- python linux/ci_check.py wayland
    linux/ci_niri.sh  (в контейнере Arch, см. .github/workflows/niri.yml)

x11: мониторы, положение, кнопки и хоткей на Xvfb, события шлёт xdotool.
wayland: настоящий mutter и PipeWire. Портал спрашивает экран окном, поэтому поток берём напрямую у mutter
(org.gnome.Mutter.ScreenCast, тот же режим метаданных): проверяется всё, кроме разговора с порталом — он в selftest.py.
niri: настоящий niri 26.04 на виртуальной видеокарте vkms, кадры DMA-BUF с модификатором."""
import glob
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import linuxcursor as lc  # noqa: E402


def wait(check, what, seconds=15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if value := check():
            return value
        time.sleep(0.05)
    raise SystemExit(f"не дождался: {what}")


def xdotool(*args):
    subprocess.run(["xdotool", *map(str, args)], check=True)


def check_x11():
    lc.WAYLAND = False
    mons = lc.list_monitors()
    print("мониторы:", mons)
    assert mons and mons[0]["width"] > 0, mons
    d = lc.primary(mons)
    assert lc.find_display("auto", [d["id"]])["id"] == d["id"]

    logger = lc.CursorLogger(d, 240)
    logger.start()
    xdotool("mousemove", 100, 200)
    wait(lambda: logger.samples and logger.samples[-1][1:3] == (100, 200), "положение курсора в логе")
    xdotool("mousemove", 300, 150, "mousedown", 1)
    wait(lambda: any(c[1:3] == ("L", 1) for c in logger.clicks), "нажатие левой кнопки")
    xdotool("mouseup", 1, "mousedown", 3)  # не click: он быстрее шага опроса, живой палец так не нажимает
    wait(lambda: any(c[1:3] == ("R", 1) for c in logger.clicks), "нажатие правой кнопки")
    xdotool("mouseup", 3)
    time.sleep(0.2)
    logger.stop()
    rate = len(logger.samples) / ((logger.samples[-1][0] - logger.samples[0][0]) / 1e9)
    print(f"сэмплов: {len(logger.samples)}, {rate:.0f} Гц, клики: {[c[1:3] for c in logger.clicks]}")
    assert rate > 120, rate
    log = logger.to_dict(logger.samples[0][0])
    assert log["samples"] and log["clicks"] and log["types"][:len(lc.TYPES)] == lc.TYPES

    hits = []
    hk = lc.HotkeyThread("ctrl+F9", lambda: hits.append(1)).start()
    xdotool("key", "ctrl+F9")
    wait(lambda: hits, "глобальный хоткей")
    xdotool("keydown", "ctrl+F9")  # держат дольше задержки автоповтора: запись должна переключиться один раз
    time.sleep(1.5)
    xdotool("keyup", "ctrl+F9")
    time.sleep(0.3)
    assert len(hits) == 2, ("удержание хоткея сработало несколько раз", len(hits))
    busy = lc.HotkeyThread("ctrl+F9", lambda: None)  # занято этой же программой, X11 откажет второму клиенту
    try:
        busy.start()
    except RuntimeError as e:
        print("занятый хоткей:", e)
    else:
        raise SystemExit("второй захват хоткея должен был упасть")
    hk.stop()

    xdotool("mousemove", 300, 150)  # Xvfb сбрасывает курсор, когда отключается его последний клиент
    x, y, shape, down, showing = lc.pointer()
    assert (x, y) == (300, 150) and showing and not down, (x, y, showing, down)
    assert lc.cursor_sprite("arrow", 32)[0].width > 0
    assert not lc.has_tray(), "у Xvfb трея нет"
    print("x11: ок")


def check_wayland():
    from jeepney import DBusAddress, MessageType, new_method_call
    from jeepney.bus_messages import MatchRule, message_bus
    from jeepney.io.blocking import open_dbus_connection

    import waylandcursor as wl
    run = tempfile.mkdtemp()
    os.chmod(run, 0o700)
    os.environ.update(XDG_RUNTIME_DIR=run, XDG_SESSION_TYPE="wayland")
    os.environ.pop("DISPLAY", None)
    procs = [subprocess.Popen(["pipewire"])]
    wait(lambda: Path(run, "pipewire-0").exists(), "сокет PipeWire")
    if shutil.which("wireplumber"):
        procs.append(subprocess.Popen(["wireplumber"]))
    procs.append(subprocess.Popen(["mutter", "--headless", "--wayland", "--no-x11", "--virtual-monitor", "1280x720"]))
    conn = open_dbus_connection(bus="SESSION")

    def call(addr, method, sig=None, *body):
        reply = conn.send_and_get_reply(new_method_call(addr, method, sig, body), timeout=20)
        assert reply.header.message_type != MessageType.error, (method, reply.body)
        return reply.body

    bus = "org.gnome.Mutter.ScreenCast"
    names = lambda: call(DBusAddress("/org/freedesktop/DBus", "org.freedesktop.DBus", "org.freedesktop.DBus"), "ListNames")[0]
    try:
        wait(lambda: bus in names(), "mutter на шине", 30)
        rd_bus = "org.gnome.Mutter.RemoteDesktop"
        rd_path = call(DBusAddress("/org/gnome/Mutter/RemoteDesktop", rd_bus, rd_bus), "CreateSession")[0]
        rd = DBusAddress(rd_path, rd_bus, rd_bus + ".Session")
        rd_id = call(DBusAddress(rd_path, rd_bus, "org.freedesktop.DBus.Properties"), "Get", "ss", rd_bus + ".Session",
                     "SessionId")[0][1]
        sc_path = call(DBusAddress("/org/gnome/Mutter/ScreenCast", bus, bus), "CreateSession", "a{sv}",
                       {"remote-desktop-session-id": ("s", rd_id)})[0]
        sc = DBusAddress(sc_path, bus, bus + ".Session")
        stream_path = call(sc, "RecordMonitor", "sa{sv}", "", {"cursor-mode": ("u", 2)})[0]  # 2 — метаданные
        rule = MatchRule(type="signal", interface=bus + ".Stream", member="PipeWireStreamAdded", path=stream_path)
        conn.send_and_get_reply(message_bus.AddMatch(rule))
        with conn.filter(rule) as q:
            call(rd, "Start")
            node = conn.recv_until_filtered(q, timeout=20).body[0]
        print("узел PipeWire:", node)

        s = socket.socket(socket.AF_UNIX)
        s.connect(str(Path(run, "pipewire-0")))
        src = wl.WaylandSource.__new__(wl.WaylandSource)
        src.state, src.shapes, src.shape, src.buttons, src.error = (0, 0, None, False), {}, None, None, None
        src.stream = wl.CursorStream(s.detach(), node, src._cursor, src._failed)
        for x, y in ((100, 200), (640, 360), (900, 111)):
            call(rd, "NotifyPointerMotionAbsolute", "sdd", stream_path, float(x), float(y))
            wait(lambda: src.read()[:2] == (x, y), f"курсор в ({x}, {y}); сейчас {src.read()[:4]}, ошибка {src.error}")
            print("курсор:", src.read()[:4])
        shape = src.read()[2]
        name, sprite = src.describe(shape)
        assert sprite and sprite[0].width > 0 and src.read()[3], "картинка курсора из метаданных"
        print("картинка:", sprite[0].size, "hotspot", sprite[1])
        src.stream.close()
        print("wayland: ок")
    finally:
        for p in reversed(procs):
            p.terminate()


class UinputTablet:
    """Абсолютная мышь через /dev/uinput, как планшет в QEMU: udev и libinput считают её обычной мышью."""
    SPAN = 4096

    def __init__(self):
        import fcntl
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        for ioctl, value in ((0x40045564, 1), (0x40045565, 0x110), (0x40045564, 3)):  # EV_KEY, BTN_LEFT, EV_ABS
            fcntl.ioctl(self.fd, ioctl, value)
        for axis in (0, 1):  # UI_SET_ABSBIT и UI_ABS_SETUP: code, value, min, max, fuzz, flat, resolution
            fcntl.ioctl(self.fd, 0x40045567, axis)
            fcntl.ioctl(self.fd, 0x401c5504, struct.pack("<H2x6i", axis, 0, 0, self.SPAN - 1, 0, 0, 0))
        fcntl.ioctl(self.fd, 0x405c5503, struct.pack("<4H80sI", 6, 1, 1, 1, b"smooth-cursor-ci", 0))  # UI_DEV_SETUP
        fcntl.ioctl(self.fd, 0x5501)  # UI_DEV_CREATE
        time.sleep(1)  # пусть udev разметит устройство

    def move(self, x, y):
        os.write(self.fd, b"".join(struct.pack("<qqHHi", 0, 0, *e) for e in ((3, 0, x), (3, 1, y), (0, 0, 0))))


def check_niri():
    """niri 26.04 на виртуальной видеокарте vkms (.github/workflows/niri.yml): кадры только DMA-BUF с модификатором.
    Проверяется то, что ломалось, — согласование формата, — и что положение и картинка курсора доходят. RemoteDesktop
    у niri нет, поэтому курсор двигает виртуальная мышь через /dev/uinput."""
    from jeepney import DBusAddress, MessageType, new_method_call
    from jeepney.bus_messages import MatchRule, message_bus
    from jeepney.io.blocking import open_dbus_connection

    import waylandcursor as wl
    run = tempfile.mkdtemp()
    os.chmod(run, 0o700)
    os.environ.update(XDG_RUNTIME_DIR=run, XDG_SESSION_TYPE="wayland", RUST_LOG="niri=debug")
    os.environ.pop("DISPLAY", None)
    cfg = Path(run, "niri.kdl")
    cfg.write_text("debug {\n    dbus-interfaces-in-non-session-instances\n}\n")
    mouse = UinputTablet()  # до niri: libinput найдёт её при запуске, без горячего подключения
    procs = [subprocess.Popen(["pipewire"])]
    wait(lambda: Path(run, "pipewire-0").exists(), "сокет PipeWire")
    procs.append(subprocess.Popen(["wireplumber"]))
    time.sleep(1)  # niri включает трансляцию, только если PipeWire уже поднят
    procs.append(subprocess.Popen(["niri", "-c", str(cfg)], stdout=open("niri.log", "w"), stderr=subprocess.STDOUT))
    conn = open_dbus_connection(bus="SESSION")

    def call(addr, method, sig=None, *body):
        reply = conn.send_and_get_reply(new_method_call(addr, method, sig, body), timeout=20)
        assert reply.header.message_type != MessageType.error, (method, reply.body)
        return reply.body

    bus = "org.gnome.Mutter.ScreenCast"
    names = lambda: call(DBusAddress("/org/freedesktop/DBus", "org.freedesktop.DBus", "org.freedesktop.DBus"), "ListNames")[0]
    try:
        wait(lambda: procs[-1].poll() is None and bus in names() or procs[-1].poll() is not None, "niri на шине", 30)
        assert procs[-1].poll() is None, "niri упал, см. niri.log"
        sock = wait(lambda: glob.glob(f"{run}/niri.*.sock"), "сокет niri")[0]
        msg = lambda *a: subprocess.run(["niri", "msg", *a], check=True, capture_output=True, text=True,
                                        env={**os.environ, "NIRI_SOCKET": sock}).stdout
        outputs = json.loads(msg("--json", "outputs"))
        # у раннера есть ещё экран Hyper-V: vblank он не шлёт, кадры после первого не идут. Оставляем только vkms,
        # тогда и абсолютная мышь (niri растягивает её на все экраны) ложится ровно на него
        connector = next(n for n in outputs if n.startswith("Virtual"))
        for other in set(outputs) - {connector}:
            msg("output", other, "off")
        w, h = outputs[connector]["logical"]["width"], outputs[connector]["logical"]["height"]
        print("экран niri:", connector, w, h, "выключены:", sorted(set(outputs) - {connector}))
        sc_path = call(DBusAddress("/org/gnome/Mutter/ScreenCast", bus, bus), "CreateSession", "a{sv}", {})[0]
        sc = DBusAddress(sc_path, bus, bus + ".Session")
        stream_path = call(sc, "RecordMonitor", "sa{sv}", connector, {"cursor-mode": ("u", 2)})[0]  # 2 — метаданные
        rule = MatchRule(type="signal", interface=bus + ".Stream", member="PipeWireStreamAdded", path=stream_path)
        conn.send_and_get_reply(message_bus.AddMatch(rule))
        with conn.filter(rule) as q:
            call(sc, "Start")
            node = conn.recv_until_filtered(q, timeout=20).body[0]
        print("узел PipeWire:", node)

        s = socket.socket(socket.AF_UNIX)
        s.connect(str(Path(run, "pipewire-0")))
        src = wl.WaylandSource.__new__(wl.WaylandSource)
        src.state, src.shapes, src.shape, src.buttons, src.error = (0, 0, None, False), {}, None, None, None
        got = []  # что приходило в метаданных: для разбора, если курсор так и не появится
        src.stream = wl.CursorStream(s.detach(), node, lambda p: (got.append(p and (*p[:4], p[4] and p[4][0])),
                                                                     src._cursor(p)), src._failed)
        wait(lambda: got, "метаданные курсора от niri", 20)  # до исправления: niri «no more input formats»
        for x, y in ((100, 200), (512, 384), (900, 111)):
            def near():  # двигаем на каждом шаге: одиночное событие могло прийти, пока niri не слушал
                mouse.move(x * mouse.SPAN // w + 1, y * mouse.SPAN // h)
                mouse.move(x * mouse.SPAN // w, y * mouse.SPAN // h)
                return src.read()[3] and abs(src.read()[0] - x) <= 1 and abs(src.read()[1] - y) <= 1
            wait(near, f"курсор в ({x}, {y}); сейчас {src.read()[:4]}, ошибка {src.error}, "
                       f"метаданных {len(got)}, последние {got[-3:]}", 20)
            print("курсор:", src.read()[:4])
        name, sprite = src.describe(src.read()[2])
        assert sprite and sprite[0].width > 0, "картинка курсора из метаданных"
        print("картинка:", sprite[0].size, "hotspot", sprite[1])
        src.stream.close()
        print("niri: ок")
    finally:
        for p in reversed(procs):
            p.terminate()


if __name__ == "__main__":
    {"x11": check_x11, "wayland": check_wayland, "niri": check_niri}[sys.argv[1]]()
