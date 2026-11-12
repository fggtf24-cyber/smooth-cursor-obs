"""Проверка обновлений на GitHub Releases и скачивание установщика."""
import hashlib
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

VERSION = "1.7.0"
REPO = "fggtf24-cyber/smooth-cursor-obs"
ASSET = "SmoothCursor-Setup.exe" if sys.platform == "win32" else "SmoothCursor-linux-x86_64.tar.gz"
PAGE = f"https://github.com/{REPO}/releases/latest"


def parse(v):
    """«v1.0.3» → (1, 0, 3); мусор → (0,)."""
    try:
        return tuple(int(x) for x in v.lstrip("vV").split("."))
    except ValueError:
        return (0,)


def latest():
    """Новее ли последний релиз: {"version", "url", "size", "sha256"} или None."""
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                 headers={"User-Agent": "SmoothCursor", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        rel = json.load(r)
    v = rel.get("tag_name", "").lstrip("vV")
    asset = next((a for a in rel.get("assets", []) if a.get("name") == ASSET), None)
    if parse(v) <= parse(VERSION) or not asset:
        return None
    url = asset["browser_download_url"]
    if not url.startswith(f"https://github.com/{REPO}/releases/download/"):  # качаем только из своего репозитория
        return None
    digest = asset.get("digest") or ""
    return {"version": v, "url": url, "size": asset["size"],
            "sha256": digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else None}


def fetch(url, out, size=None, sha256=None, progress=lambda pct: None):
    """Скачивает файл и сверяет размер и SHA256 (если они известны). Возвращает путь."""
    h, done = hashlib.sha256(), 0
    req = urllib.request.Request(url, headers={"User-Agent": "SmoothCursor"})
    with urllib.request.urlopen(req, timeout=30) as r, open(out, "wb") as f:
        while chunk := r.read(1 << 16):
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if size:
                progress(done * 100 / size)
    if (size and done != size) or (sha256 and h.hexdigest() != sha256):
        Path(out).unlink(missing_ok=True)
        raise RuntimeError("файл скачался с ошибкой (не совпала контрольная сумма)")
    return out


def download(info, progress=lambda pct: None):
    """Скачивает установщик во временную папку. Возвращает путь."""
    out = Path(tempfile.gettempdir()) / f"SmoothCursor-Setup-{info['version']}.exe"
    return fetch(info["url"], out, info["size"], info["sha256"], progress)
