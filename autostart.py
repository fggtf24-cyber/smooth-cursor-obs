"""Запуск вместе с OBS и единственная копия программы. Только стандартная библиотека: фоновый процесс (--watch) висит
весь сеанс Windows и не должен тянуть numpy и окно.

Автозагрузка Windows запускает «Smooth Cursor.exe --watch». Он раз в 2 с смотрит, не появился ли OBS, и запускает
программу с --with-obs. Если её закрыли при открытом OBS, заново не запустит — только при следующем запуске OBS."""
import ctypes as C
import ctypes.wintypes as W
import os
import subprocess
import sys
import winreg
from pathlib import Path

RUN_KEY, RUN_NAME = r"Software\Microsoft\Windows\CurrentVersion\Run", "Smooth Cursor"
STOP = "Local\\SmoothCursorWatchStop"  # событие: выставили — фоновый процесс выходит; занято — он уже запущен
SHOW = "Local\\SmoothCursorShow"       # событие: вторая копия просит первую показать окно
LOCK = "Local\\SmoothCursorApp"        # мьютекс: занят — программа уже запущена
ALREADY_EXISTS = 183

k32 = C.WinDLL("kernel32", use_last_error=True)
k32.CreateEventW.argtypes = [C.c_void_p, W.BOOL, W.BOOL, W.LPCWSTR]
k32.CreateEventW.restype = W.HANDLE
k32.CreateMutexW.argtypes = [C.c_void_p, W.BOOL, W.LPCWSTR]
k32.CreateMutexW.restype = W.HANDLE
k32.SetEvent.argtypes = k32.CloseHandle.argtypes = [W.HANDLE]
k32.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
k32.WaitForSingleObject.restype = W.DWORD
k32.OpenProcess.restype = W.HANDLE
k32.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]


def processes(name):
    """(pid, полный путь) запущенных программ с таким именем exe (без учёта регистра)."""
    ids, got = (W.DWORD * 4096)(), W.DWORD()
    C.windll.psapi.EnumProcesses(ids, C.sizeof(ids), C.byref(got))
    for pid in ids[:got.value // 4]:
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            buf, n = C.create_unicode_buffer(1024), W.DWORD(1024)
            ok = k32.QueryFullProcessImageNameW(h, 0, buf, C.byref(n))
            k32.CloseHandle(h)
            if ok and buf.value.lower().endswith("\\" + name.lower()):
                yield pid, buf.value


def running_exe(name):
    """Полный путь запущенной программы с таким именем exe, иначе None."""
    return next((path for _, path in processes(name)), None)


def obs_running():
    """pid запущенных OBS: закрытый OBS бывает, что висит процессом ещё долго, рядом с уже открытым новым."""
    return {pid for pid, _ in processes("obs64.exe")}


def obs_exe():
    """obs64.exe: папку установки OBS пишет в реестр его установщик; нет записи — стандартная папка."""
    dirs = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\OBS Studio") as k:
                dirs.append(Path(winreg.QueryValue(k, None)))
        except OSError:
            pass
    dirs += [Path(os.environ.get(v, "")) / "obs-studio" for v in ("ProgramFiles", "ProgramW6432")]
    return next((e for e in (d / "bin" / "64bit" / "obs64.exe" for d in dirs) if e.is_file()), None)


def launch_obs():
    """Запустить OBS. False — не нашёлся. Папка запуска — его bin\\64bit: из другой он не находит свои файлы."""
    exe = obs_exe()
    if exe:
        subprocess.Popen([str(exe)], cwd=exe.parent, creationflags=0x08 | 0x200)  # DETACHED | NEW_PROCESS_GROUP
    return bool(exe)


def command(*args):
    """Как запустить эту программу: установленную или из исходников."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [str(Path(sys.executable).with_name("pythonw.exe")), str(Path(__file__).with_name("app.pyw")), *args]


def signal(name):
    h = k32.CreateEventW(None, True, False, name)
    k32.SetEvent(h)
    k32.CloseHandle(h)


def watch(period_ms=2000):
    """Фоновый процесс: запускает программу, когда открывается OBS. Выходит по событию STOP."""
    stop = k32.CreateEventW(None, True, False, STOP)
    if C.get_last_error() == ALREADY_EXISTS:
        return
    seen = set()  # OBS уже открыт при старте — тоже запускаем (вторая копия выйдет сама)
    while k32.WaitForSingleObject(stop, period_ms) != 0:
        now = obs_running()
        if now - seen:
            subprocess.Popen(command("--with-obs"))
        seen = now
    k32.CloseHandle(stop)


def sync(on):
    """Включает или выключает запуск вместе с OBS: запись в автозагрузке и фоновый процесс сразу, без перезахода."""
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(command("--watch")))
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass
    if on:
        subprocess.Popen(command("--watch"))  # уже запущен — новый сразу выйдет
    else:
        signal(STOP)


def on_show(callback):
    """Ждёт просьбу показать окно (вторая копия программы) и зовёт callback. Для отдельного потока."""
    h = k32.CreateEventW(None, False, False, SHOW)
    while k32.WaitForSingleObject(h, 0xFFFFFFFF) == 0:
        callback()


def boot():
    """Первым делом при старте app.pyw: фоновый режим либо проверка, что программа ещё не запущена."""
    if "--watch" in sys.argv:
        watch()
        sys.exit()
    k32.CreateMutexW(None, False, LOCK)  # живёт, пока жив процесс
    if C.get_last_error() == ALREADY_EXISTS:
        if "--with-obs" not in sys.argv:  # открыли ярлык, а программа в трее — показать её окно
            signal(SHOW)
        sys.exit()
