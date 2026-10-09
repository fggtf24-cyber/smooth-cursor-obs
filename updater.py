"""Проверка обновлений на GitHub Releases и скачивание установщика."""
import hashlib
import json
import tempfile
import urllib.request
from pathlib import Path

VERSION = "1.1.1"
REPO = "fggtf24-cyber/smooth-cursor-obs"
ASSET = "SmoothCursor-Setup.exe"
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


def download(info, progress=lambda pct: None):
    """Скачивает установщик во временную папку, сверяет размер и SHA256. Возвращает путь."""
    out = Path(tempfile.gettempdir()) / f"SmoothCursor-Setup-{info['version']}.exe"
    h, done = hashlib.sha256(), 0
    req = urllib.request.Request(info["url"], headers={"User-Agent": "SmoothCursor"})
    with urllib.request.urlopen(req, timeout=30) as r, open(out, "wb") as f:
        while chunk := r.read(1 << 16):
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            progress(done * 100 / info["size"])
    if done != info["size"] or (info["sha256"] and h.hexdigest() != info["sha256"]):
        out.unlink(missing_ok=True)
        raise RuntimeError("файл обновления повреждён (не совпала контрольная сумма)")
    return out
