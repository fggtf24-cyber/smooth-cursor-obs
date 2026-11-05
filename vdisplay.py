"""Виртуальный монитор на драйвере Virtual Display Driver (github.com/VirtualDrivers/Virtual-Display-Driver, MIT) — том
же, которым управляет VDD Control. Экран включается и выключается как обычный дисплей в параметрах Windows, без прав
администратора: ноль мониторов сам драйвер не умеет (считает за один). Права нужны, только чтобы поставить драйвер или
включить отключённое устройство — один запрос Windows."""
import ctypes as C
import os
import re
import struct
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

import updater
import win32cursor as wc
from i18n import t

PIPE = r"\\.\pipe\MTTVirtualDisplayPipe"
FOLDER = r"C:\VirtualDisplayDriver"  # там драйвер ищет vdd_settings.xml
ARM = "ARM64" in (os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE", "")).upper()
_REL = "https://github.com/VirtualDrivers/Virtual-Display-Driver/releases/download/25.7.23/"
# Версии закреплены вместе с хэшами. «x86» в имени — так автор называет сборку для x64
DRIVER = (_REL + "VirtualDisplayDriver-ARM64.Driver.Only.zip",
          "dcd487161469c726bab60f174a7e7f70ee02afd437cad0d5c0e6755953de5a25") if ARM else \
         (_REL + "VirtualDisplayDriver-x86.Driver.Only.zip",
          "e24210692b442b39af763536330ce78b423f19342b7a7792c26de3944e418b3a")
NEFCON = ("https://github.com/nefarius/nefcon/releases/download/v1.17.40/nefcon_v1.17.40.zip",
          "812bae7ed7dfb7d6d2284bc7de2f8ccebc92ed2a0b1ae893c53b337096e50c1a")
SIZES = {"1920x1080": "1080p", "2560x1440": "1440p", "3840x2160": "4K"}
RATES = (30, 60, 120, 144, 240)
MIN_W, MIN_H, MAX_SIDE = 640, 480, 8192  # больше 8192 не кодируют видеокарты


def q(s):
    """Строка для PowerShell."""
    return "'" + str(s).replace("'", "''") + "'"


def alive():
    """Запущен ли драйвер: его канал управления открывается, только пока он работает."""
    try:
        with open(PIPE, "r+b", buffering=0) as p:
            p.write("PING".encode("utf-16-le"))
        return True
    except OSError:
        return False


def monitor():
    """Виртуальный монитор, если он сейчас есть в Windows."""
    return next((m for m in wc.list_monitors() if m["virtual"]), None)


def device():
    """Устройство драйвера в системе (бывает и отключённым), иначе None."""
    ps = ("Get-PnpDevice -Class Display -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $_.HardwareID "
          "-match '^Root\\\\MttVDD$' } | Select-Object -First 1 -ExpandProperty InstanceId")
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                          text=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=30).stdout.strip() or None


def elevated(script):
    """Скрипт PowerShell с правами администратора. RuntimeError — отказали в запросе или скрипт не отработал."""
    with tempfile.TemporaryDirectory(prefix="smoothcursor-") as tmp:
        ps1, out = Path(tmp) / "run.ps1", Path(tmp) / "run.log"
        ps1.write_text(f"Start-Transcript -Path {q(out)} -Force | Out-Null\n$ErrorActionPreference = 'Stop'\n{script}\n"
                       "Stop-Transcript | Out-Null\nexit 0\n", encoding="utf-8-sig")
        args = ",".join(map(q, ("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", f'"{ps1}"')))
        launcher = (f"$p = Start-Process powershell -Verb RunAs -Wait -PassThru -WindowStyle Hidden -ArgumentList {args};"
                    " exit $p.ExitCode")
        try:
            code = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", launcher],
                                  capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=300).returncode
        except subprocess.TimeoutExpired:
            raise RuntimeError(t("Windows не ответила за 5 минут"))
        if code:
            if not out.exists():
                raise RuntimeError(t("Windows не дала прав администратора — запрос отклонён"))
            tail = out.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-12:-5]
            raise RuntimeError(" ".join(s.strip() for s in tail if s.strip()) or f"exit {code}")


def install(status):
    """Ставит драйвер так же, как VDD Control: nefcon создаёт устройство и привязывает к нему драйвер."""
    with tempfile.TemporaryDirectory(prefix="smoothcursor-") as tmp:
        tmp = Path(tmp)
        for name, (url, sha) in (("driver", DRIVER), ("nefcon", NEFCON)):
            status(t("Скачиваю драйвер…"))
            updater.fetch(url, tmp / f"{name}.zip", sha256=sha)
            with zipfile.ZipFile(tmp / f"{name}.zip") as z:
                z.extractall(tmp / name)
        pkg = next((tmp / "driver").rglob("MttVDD.inf")).parent
        nefcon = tmp / "nefcon" / ("ARM64" if ARM else "x64") / "nefconc.exe"
        status(t("Ставлю драйвер — подтвердите запрос Windows…"))
        elevated(f"""
$pkg = {q(pkg)}; $base = {q(FOLDER)}
New-Item -ItemType Directory -Force -Path $base | Out-Null
Get-ChildItem -File $pkg | Where-Object {{ $_.Name -ne 'vdd_settings.xml' -or -not (Test-Path "$base\\vdd_settings.xml") }} |
    Copy-Item -Destination $base -Force
New-Item 'HKLM:\\SOFTWARE\\MikeTheTech\\VirtualDisplayDriver' -Force | Out-Null
Set-ItemProperty 'HKLM:\\SOFTWARE\\MikeTheTech\\VirtualDisplayDriver' -Name VDDPATH -Value $base
# Драйвер подписан, но не Microsoft: без доверия издателю Windows спросит отдельным окном, а наше скрыто
$store = New-Object Security.Cryptography.X509Certificates.X509Store('TrustedPublisher', 'LocalMachine')
$store.Open('ReadWrite'); $store.Add((Get-AuthenticodeSignature "$pkg\\MttVDD.dll").SignerCertificate); $store.Close()
& {q(nefcon)} install "$pkg\\MttVDD.inf" 'Root\\MttVDD' --no-duplicates --remove-duplicates
if ($LASTEXITCODE -notin 0, 3010) {{ throw "nefcon: $LASTEXITCODE" }}
""")


def wait(check, seconds):
    """Значение check(), как только оно истинно; за отведённое время не дождались — None."""
    end = time.monotonic() + seconds
    while True:
        if v := check():
            return v
        if time.monotonic() > end:
            return None
        time.sleep(0.25)


def change(device_name, fields, w=0, h=0, hz=0, x=0, flags=1):
    """Настройка экрана структурой DEVMODEW. flags: 1 — CDS_UPDATEREGISTRY, запомнить. True — получилось."""
    dm = bytearray(220)
    struct.pack_into("<H", dm, 68, len(dm))  # dmSize
    struct.pack_into("<I", dm, 72, fields)   # dmFields
    struct.pack_into("<ii", dm, 76, x, 0)    # dmPosition
    struct.pack_into("<IIII", dm, 172, w, h, 0, hz)  # dmPelsWidth, dmPelsHeight, dmDisplayFlags, dmDisplayFrequency
    return wc.user32.ChangeDisplaySettingsExW(device_name, (C.c_char * len(dm)).from_buffer(dm), None, flags, None) == 0


def parse_size(text):
    """«5120x2160», «5120×2160», «5120 на 2160» → «5120x2160». ValueError — понятным текстом."""
    m = re.fullmatch(r"\s*(\d+)\D+(\d+)\s*", text)
    if not m:
        raise ValueError(t("Напишите ширину и высоту, например 5120x2160"))
    w, h = int(m[1]) // 2 * 2, int(m[2]) // 2 * 2  # кодировщики видео принимают только чётные стороны
    if not (MIN_W <= w <= MAX_SIDE and MIN_H <= h <= MAX_SIDE):
        raise ValueError(t("Разрешение — от {}×{} до {}×{}").format(MIN_W, MIN_H, MAX_SIDE, MAX_SIDE))
    return f"{w}x{h}"


def mode_at(device_name, i):
    """Режим экрана (ширина, высота, частота) под номером i; -1 — текущий. Нет такого — None."""
    dm = bytearray(220)
    struct.pack_into("<H", dm, 68, len(dm))
    if not wc.user32.EnumDisplaySettingsW(device_name, i, (C.c_char * len(dm)).from_buffer(dm)):
        return None
    return *struct.unpack_from("<II", dm, 172), struct.unpack_from("<I", dm, 184)[0]


def modes(device_name):
    """Все режимы, которые драйвер предлагает Windows."""
    out, i = set(), 0
    while m := mode_at(device_name, i):
        out.add(m)
        i += 1
    return out


def set_mode(device_name, w, h, hz):
    return change(device_name, 0x580000, w, h, hz)  # DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY


def add_mode(w, h, hz):
    """Новый режим в настройки драйвера. Читает их он только при запуске устройства, а файл — для администраторов:
    правка и перезапуск под одним запросом Windows."""
    elevated(f"""
$base = (Get-ItemProperty 'HKLM:\\SOFTWARE\\MikeTheTech\\VirtualDisplayDriver' -ErrorAction SilentlyContinue).VDDPATH
if (-not $base) {{ $base = {q(FOLDER)} }}
$xml = Join-Path $base 'vdd_settings.xml'
$mode = '<resolution><width>{w}</width><height>{h}</height><refresh_rate>{hz}</refresh_rate></resolution>'
$text = [IO.File]::ReadAllText($xml)
if (-not $text.Contains('</resolutions>')) {{ throw "vdd_settings.xml: no resolutions" }}
[IO.File]::WriteAllText($xml, $text.Replace('</resolutions>', "    $mode`r`n    </resolutions>"))
$dev = {q(device())}
Disable-PnpDevice -InstanceId $dev -Confirm:$false
Start-Sleep -Seconds 1
Enable-PnpDevice -InstanceId $dev -Confirm:$false
""")


def place(device_name, w, h, x):
    """Подключает экран к рабочему столу или, при w = h = 0, отключает — как в «Параметры → Дисплей»."""
    ok = change(device_name, 0x180020, w, h, x=x, flags=0x10000001)  # DM_POSITION | W | H; запомнить, применить позже
    return ok and wc.user32.ChangeDisplaySettingsExW(None, None, None, 0, None) == 0


def output():
    """Имя экрана драйвера: есть и у отключённого от рабочего стола."""
    return next(iter(wait(wc.virtual_devices, 10) or ()), None)


def apply(size, hz, status):
    """Подключает экран с таким разрешением и частотой, новые режимы добавляет в драйвер. Возвращает монитор."""
    w, h = map(int, size.split("x"))
    name = output()
    if not name:
        raise RuntimeError(t("монитор не появился"))
    if (w, h, hz) not in modes(name):
        status(t("Добавляю разрешение — подтвердите запрос Windows…"))
        add_mode(w, h, hz)
        status(t("Запускаю драйвер…"))
        if not wait(alive, 20):
            raise RuntimeError(t("драйвер не запустился — возможно, нужно перезагрузить компьютер"))
        wait(monitor, 5)  # Windows вернёт экран сама, если он был подключён
        name = output()
        if not name or (w, h, hz) not in modes(name):
            raise RuntimeError(t("драйвер не принял разрешение {} {} Гц").format(size, hz))
    if not monitor():
        right = max(m["left"] + m["width"] for m in wc.list_monitors())
        if not place(name, w, h, right) or not wait(monitor, 10):
            raise RuntimeError(t("монитор не появился"))
    d = monitor()
    if mode_at(d["device"], -1) != (w, h, hz):
        if not set_mode(d["device"], w, h, hz):
            raise RuntimeError(t("Windows не приняла разрешение {} {} Гц").format(size, hz))
        wait(lambda: (m := monitor()) and (m["width"], m["height"]) == (w, h), 5)
    return monitor()


def turn_on(size, hz, status):
    """Делает так, чтобы виртуальный монитор был и с нужным режимом: при необходимости ставит или включает драйвер."""
    if not alive():
        dev = device()
        if dev:
            status(t("Включаю драйвер — подтвердите запрос Windows…"))
            # не pnputil /enable-device: после перезагрузки у отключённого устройства нет живого узла, и он отвечает 1167
            elevated(f"Enable-PnpDevice -InstanceId {q(dev)} -Confirm:$false")
        else:
            install(status)
        status(t("Запускаю драйвер…"))
        if not wait(alive, 20):
            raise RuntimeError(t("драйвер не запустился — возможно, нужно перезагрузить компьютер"))
        wait(monitor, 5)  # только что запущенному драйверу Windows подключает экран сама
    status(t("Включаю монитор…"))
    return apply(size, hz, status)


def turn_off():
    """Отключает экран от рабочего стола. Драйвер остаётся работать, поэтому включение потом не спрашивает права."""
    if d := monitor():
        if not place(d["device"], 0, 0, 0) or not wait(lambda: not monitor(), 10):
            raise RuntimeError(t("монитор не выключился"))
