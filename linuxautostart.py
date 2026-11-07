"""Запуск вместе с OBS и единственная копия программы на Linux. Только стандартная библиотека: фоновый процесс висит весь
сеанс и не должен тянуть numpy и окно. Логика как в win32autostart: --watch следит за OBS и запускает программу."""
import contextlib
import os
import shlex
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

AUTOSTART = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / "smooth-cursor.desktop"


def runtime(name):
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return str(Path(base) / f"smooth-cursor-{os.getuid()}-{name}")


def processes(name):
    """(pid, полный путь) запущенных программ с таким именем."""
    for p in Path("/proc").glob("[0-9]*"):
        with contextlib.suppress(OSError):
            if (p / "comm").read_text().strip() == name[:15]:  # в comm ядро хранит 15 символов
                yield int(p.name), os.readlink(p / "exe")


def running_exe(name):
    """Полный путь запущенной программы с таким именем, иначе None."""
    return next((path for _, path in processes(name)), None)


def obs_running():
    return {pid for pid, _ in processes("obs")}


def command(*args):
    """Как запустить эту программу: установленную или из исходников."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, str(Path(__file__).with_name("app.pyw")), *args]


def signal(name):
    with contextlib.suppress(OSError), socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
        s.sendto(b"1", runtime(name))


def listen(name):
    path = runtime(name)
    with contextlib.suppress(OSError):
        os.unlink(path)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    s.bind(path)
    return s


_locks = []  # блокировка живёт, пока открыт файл


def take_lock(name):
    """True — мы первые: блокировку держим до конца процесса."""
    import fcntl
    f = open(runtime(name), "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return False
    _locks.append(f)
    return True


def watch(period=2):
    """Фоновый процесс: запускает программу, когда открывается OBS. Выходит по сигналу stop."""
    if not take_lock("watch"):
        return
    stop = listen("stop")
    stop.settimeout(period)
    seen = set()  # OBS уже открыт при старте — тоже запускаем (вторая копия выйдет сама)
    while True:
        try:
            stop.recv(1)
            return
        except TimeoutError:
            pass
        now = obs_running()
        if now - seen:
            subprocess.Popen(command("--with-obs"))
        seen = now


def sync(on):
    """Включает или выключает запуск вместе с OBS: файл автозапуска и фоновый процесс."""
    if on:
        AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART.write_text("[Desktop Entry]\nType=Application\nName=Smooth Cursor\nNoDisplay=true\n"
                             f"Exec={shlex.join(command('--watch'))}\n", encoding="utf-8")
        subprocess.Popen(command("--watch"))  # уже запущен — новый сразу выйдет
    else:
        AUTOSTART.unlink(missing_ok=True)
        signal("stop")


def on_show(callback):
    """Ждёт просьбу показать окно (вторая копия программы) и зовёт callback. Для фонового потока."""
    s = listen("show")
    while s.recv(1):
        callback()


def boot():
    """Первым делом при старте app.pyw: фоновый режим либо проверка, что программа ещё не запущена."""
    if getattr(sys, "frozen", False):  # пути к библиотекам сборки не должны достаться ffmpeg и xdg-open
        os.environ.pop("LD_LIBRARY_PATH", None)
        if orig := os.environ.get("LD_LIBRARY_PATH_ORIG"):
            os.environ["LD_LIBRARY_PATH"] = orig
    if "--watch" in sys.argv:
        watch()
        sys.exit()
    if not take_lock("app"):
        if "--with-obs" not in sys.argv:  # открыли ярлык, а программа в трее — показать её
            signal("show")
        sys.exit()
