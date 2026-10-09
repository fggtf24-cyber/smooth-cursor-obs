"""Настройки: значения по умолчанию, поверх — config.yaml (его пишет программа при любом изменении)."""
import copy
import os
from pathlib import Path

import yaml

# Настройки пользователя — в %APPDATA%: программа может стоять в Program Files, куда писать нельзя
PATH = Path(os.environ.get("APPDATA", Path.home())) / "SmoothCursor" / "config.yaml"
LEGACY = Path(__file__).with_name("config.yaml")  # где лежал конфиг раньше — подхватим при первом запуске

DEFAULTS = {
    "obs": {"host": "localhost", "port": 4455, "password": "", "disable_capture_cursor": True},
    "hotkey": "F9",
    "display": "auto",  # auto | 5120x2160 | DISPLAY2 | часть ID монитора
    "logger": {"hz": 240},
    "sync": {"offset_ms": {30: -50, 60: -15, "default": -30}},  # по fps видео
    "smoothing": {
        "method": "spring",       # spring | one_euro
        "stiffness": 400,         # пружина: больше — быстрее догоняет (задержка ≈ 2/√stiffness с)
        "damping_ratio": 1.0,     # 1 — критическое (без перелёта), меньше — с лёгким перелётом
        "click_pull_ms": 150,     # притяжение к реальной точке вокруг клика
        "deadzone_px": 3,         # гасить дрожание меньше этого радиуса
        "one_euro": {"min_cutoff": 1.0, "beta": 0.005, "d_cutoff": 1.0},
    },
    "render": {
        "ffmpeg": "auto",         # auto — встроенный в программу, затем PATH; или путь к ffmpeg.exe
        "codec": "auto",          # auto — лучший доступный: hevc_nvenc → hevc_amf → hevc_qsv → libx264
        "cq": 18,
        "preset": "p5",
        "cursor_scale": 1.0,
        "click_animation": True,
        "click_scale": 0.82,      # до какого масштаба сжимается при клике
        "click_tilt_deg": 15,     # наклон против часовой (минус — по часовой)
        "click_ms": 320,
        "motion_blur": True,
        "blur_length": 0.5,       # длина шлейфа в долях кадра (0.5 — затвор 180°)
        "blur_opacity": 1.0,      # плотность шлейфа
        "debug_raw": False,
        "export_keyframes": False,
    },
    "ui": {"folder": "", "auto_render": True, "preview_start": 0.0, "preview_len": 5.0, "lang": "ru",
           "onboarded": False},  # folder — папка записей OBS
}


def _merge(base, over):
    for k, v in (over or {}).items():
        base[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return base


def load(path=PATH):
    cfg = copy.deepcopy(DEFAULTS)
    path = Path(path)
    if not path.exists() and path == PATH and LEGACY.exists():
        path = LEGACY
    if path.exists():
        _merge(cfg, yaml.safe_load(path.read_text(encoding="utf-8")))
        if path == LEGACY:  # программа уже настроена раньше — онбординг не нужен
            cfg["ui"]["onboarded"] = True
    cfg["obs"].pop("profile", None)  # устарело: профиль OBS больше не переключается, берётся текущий
    cfg["smoothing"].pop("damping", None)  # устарело: теперь damping_ratio
    off = cfg["sync"]["offset_ms"]
    if not isinstance(off, dict):  # старый формат — одно число
        cfg["sync"]["offset_ms"] = {30: off, 60: off, "default": off}
    return cfg


def save(cfg, path=PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
    Path(path).write_text("# Настройки Smooth Cursor. Удобнее менять в программе (app.pyw), описание — в README.\n"
                          + text, encoding="utf-8")
