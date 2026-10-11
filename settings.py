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
        "stiffness": 400,         # пружина: больше — точнее за рукой (сглаживание без задержки, разгон ≈ 3.4/√k с)
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
        "motion_tilt_right_deg": 0,  # поворот на ходу от позы Windows в сторону движения, °: едет вправо — вправо
        "motion_tilt_left_deg": 0,   # едет влево — влево (минус — назад, как от инерции), 0 — выключен
        "motion_blur": True,
        "blur_length": 0.5,       # длина шлейфа в долях кадра (0.5 — затвор 180°)
        "blur_opacity": 1.0,      # плотность шлейфа
        "blur_accurate": False,   # точный motion blur (как у камеры) вместо копий курсора — рендер дольше
        "shutter": 60,            # его выдержка, как на камере: 1/60 с
        "debug_raw": False,
        "export_keyframes": False,
    },
    "ui": {"folder": "", "auto_render": True, "preview_start": 0.0, "preview_len": 5.0, "lang": "ru",
           "onboarded": False, "skip_version": ""},  # folder — папка записей OBS; skip_version — «не сейчас»
    "my_presets": [],  # свои пресеты: [{"name": ..., значения PRESET_KEYS}], сохраняются из меню пресетов
}


# Пресеты курсора: набор значений сглаживания и эффектов. «standard» — значения по умолчанию.
_SM, _RC = DEFAULTS["smoothing"], DEFAULTS["render"]
PRESET_KEYS = [("smoothing", k) for k in ("method", "stiffness", "damping_ratio", "click_pull_ms", "deadzone_px")] + \
              [("render", k) for k in ("click_animation", "click_scale", "click_tilt_deg", "click_ms",
                                       "motion_tilt_right_deg", "motion_tilt_left_deg", "motion_blur", "blur_length",
                                       "blur_opacity")]
PRESETS = {
    "standard": {k: (_SM | _RC)[k] for _, k in PRESET_KEYS},
    "light": {"method": "spring", "stiffness": 900, "damping_ratio": 1.0, "click_pull_ms": 100, "deadzone_px": 2,
              "click_animation": True, "click_scale": 0.9, "click_tilt_deg": 8, "click_ms": 260,
              "motion_tilt_right_deg": 0, "motion_tilt_left_deg": 0, "motion_blur": True, "blur_length": 0.35, "blur_opacity": 0.8},
    "cinema": {"method": "spring", "stiffness": 180, "damping_ratio": 1.0, "click_pull_ms": 200, "deadzone_px": 4,
               "click_animation": True, "click_scale": 0.78, "click_tilt_deg": 18, "click_ms": 400,
               "motion_tilt_right_deg": 0, "motion_tilt_left_deg": 0, "motion_blur": True, "blur_length": 0.9, "blur_opacity": 1.2},
    "tilt": {k: (_SM | _RC)[k] for _, k in PRESET_KEYS} | {"motion_tilt_right_deg": 72, "motion_tilt_left_deg": 27},  # вправо = 45 + влево: зеркально
    "tilt_back": {k: (_SM | _RC)[k] for _, k in PRESET_KEYS} | {"motion_tilt_right_deg": -27,
                                                                "motion_tilt_left_deg": -27},
    "clean": {k: (_SM | _RC)[k] for _, k in PRESET_KEYS} | {"click_animation": False, "motion_blur": False},
}


def my_presets(cfg):
    """Свои пресеты по порядку сохранения (то, что не похоже на пресет, — правка конфига руками — пропускаем)."""
    return [p for p in cfg.get("my_presets") or [] if isinstance(p, dict) and isinstance(p.get("name"), str)
            and p["name"]]


def presets(cfg):
    """Встроенные пресеты, затем свои — под ключами «my:имя»."""
    return PRESETS | {"my:" + p["name"]: p for p in my_presets(cfg)}


def apply_preset(cfg, name):
    p = presets(cfg)[name]
    for sec, k in PRESET_KEYS:
        cfg[sec][k] = p.get(k, DEFAULTS[sec][k])  # в своём, сохранённом старой версией, новых ключей может не быть


def save_my_preset(cfg, name):
    """Текущие настройки — в свой пресет: с новым именем добавляется в конец, с тем же — обновляется на месте."""
    mine = my_presets(cfg)
    i = next((i for i, p in enumerate(mine) if p["name"] == name), len(mine))
    cfg["my_presets"] = mine[:i] + [{"name": name} | {k: cfg[sec][k] for sec, k in PRESET_KEYS}] + mine[i + 1:]


def delete_my_preset(cfg, name):
    cfg["my_presets"] = [p for p in my_presets(cfg) if p["name"] != name]


def preset_of(cfg):
    """Имя пресета, с которым совпадают текущие настройки, иначе None (настроено вручную)."""
    return next((n for n, p in presets(cfg).items() if all(cfg[s][k] == p.get(k) for s, k in PRESET_KEYS)), None)


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
    if (tilt := cfg["render"].pop("motion_tilt_deg", None)) is not None:  # устарело: один угол на обе стороны
        cfg["render"]["motion_tilt_right_deg"] = cfg["render"]["motion_tilt_left_deg"] = tilt
    off = cfg["sync"]["offset_ms"]
    if not isinstance(off, dict):  # старый формат — одно число
        cfg["sync"]["offset_ms"] = {30: off, 60: off, "default": off}
    return cfg


def save(cfg, path=PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
    Path(path).write_text("# Настройки Smooth Cursor. Удобнее менять в программе (app.pyw), описание — в README.\n"
                          + text, encoding="utf-8")
