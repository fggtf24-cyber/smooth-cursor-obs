"""Premiere Pro: готовое видео с плавным курсором — в открытую секвенцию, на плейхед; занято — в первую дыру после
него, куда клип влезает целиком (V1 и A1).

Снаружи Premiere команд не принимает, поэтому в нём стоит наш UXP-плагин (папка premiere/): он раз в секунду
спрашивает у программы задание по http://localhost:PORT/job, кладёт файл и отвечает на /done. Задание ждёт, пока
плагин его не заберёт: можно нажать кнопку и потом открыть Premiere.
"""
import json
import logging
import os
import subprocess
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from i18n import t

log = logging.getLogger("smooth")
PORT = 47913  # тот же — в premiere/manifest.json и index.js
PLUGIN = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "premiere"
PLUGIN_VERSION = json.loads((PLUGIN / "manifest.json").read_text(encoding="utf-8"))["version"]
UPIA = Path(os.environ.get("CommonProgramFiles", r"C:\Program Files\Common Files")) / \
    r"Adobe\Adobe Desktop Common\RemoteComponents\UPI\UnifiedPluginInstallerAgent\UnifiedPluginInstallerAgent.exe"
WAIT_S = 15  # плагин не забрал задание за это время — подсказать, что проверить

_jobs, _lock, _server, _next_id = [], threading.Lock(), None, [1]


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/job":
            return self.send_error(404)
        with _lock:
            job = _jobs.pop(0) if _jobs else None
        if not job:
            return self._reply(204)
        job["taken"].set()
        self._reply(200, {k: v for k, v in job.items() if k != "taken"})

    def do_POST(self):
        if self.path != "/done":
            return self.send_error(404)
        r = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if r.get("ok"):
            log.info(t("Premiere: видео в секвенции «%s» с %s (V1)"), r.get("sequence", "?"), timecode(r.get("at", 0)))
        else:
            log.error(t("Premiere не вставил видео: %s"), r.get("error"))
        self._reply(204)

    def _reply(self, code, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # без строки в консоль на каждый опрос
        pass


def timecode(s):
    return f"{int(s // 3600):02d}:{int(s // 60 % 60):02d}:{s % 60:05.2f}"


def send(path, duration, sequence=None):
    """Отдать видео плагину. Сервер поднимается при первом задании и живёт, пока открыта программа.
    sequence — для самопроверки: своя секвенция вместо открытой."""
    global _server
    if _server is None:
        _server = ThreadingHTTPServer(("localhost", PORT), _Handler)  # только этот компьютер
        threading.Thread(target=_server.serve_forever, daemon=True).start()
    job = {"id": _next_id[0], "path": str(path), "duration": duration, "taken": threading.Event()}
    if sequence:
        job["sequence"] = sequence
    _next_id[0] += 1
    with _lock:
        _jobs.append(job)
    log.info(t("Premiere: жду, когда заберёт %s…"), Path(path).name)

    def watch():
        if not job["taken"].wait(WAIT_S):
            log.warning(t("Premiere пока не забрал видео: он открыт, в нём открыта секвенция? Не помогает — "
                          "перезапустите Premiere, видео дождётся."))

    threading.Thread(target=watch, daemon=True).start()


def install():
    """Поставить плагин утилитой Adobe (UPIA): папка premiere/ → .ccx → /install."""
    if not UPIA.exists():
        raise RuntimeError(t("Нет установщика плагинов Adobe — установлен ли Creative Cloud?"))
    ccx = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SmoothCursor" / "SmoothCursor-Premiere.ccx"
    ccx.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ccx, "w", zipfile.ZIP_DEFLATED) as z:
        for f in PLUGIN.iterdir():
            z.write(f, f.name)
    r = subprocess.run([str(UPIA), "/install", str(ccx)], capture_output=True, text=True, errors="replace",
                       creationflags=subprocess.CREATE_NO_WINDOW)
    if "Installation Successful" not in r.stdout:  # код выхода 0 и при ошибке — смотрим ответ
        raise RuntimeError(t("установщик Adobe: {}").format((r.stdout + r.stderr).strip()[-300:]))
    log.info(t("Плагин Smooth Cursor для Premiere установлен"))
