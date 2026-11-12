"""Выбор монитора, углы экрана и лог курсора для Linux: то же, что в win32cursor.py, но без winreg и WinDLL.
win32cursor.py намеренно отдельно: порт на Linux не должен менять код под Windows."""
import base64
import io
import re
import threading
import time
from pathlib import Path

from PIL import Image

from i18n import t


def primary(mons):
    """Основной монитор: у него левый верхний угол в (0, 0)."""
    return next((m for m in mons if (m["left"], m["top"]) == (0, 0)), mons[0])


def corner_pos(mon, w, h):
    """Левый верх окна w×h в правом нижнем углу рабочей области монитора. Всё от самого экрана: отступы — под его
    масштаб, панель задач с любой стороны рабочая область уже учла. Если она занимает экран до низа, панель скрывается
    или снизу лежит док поверх экрана: поднимаем на его высоту."""
    k = mon["dpi"] / 96
    gap = round(16 * k)
    _, _, right, bottom = mon["work"]
    if bottom >= mon["top"] + mon["height"]:
        bottom -= round(56 * k)
    return right - w - gap, bottom - h - gap


def find_display(mons, select="auto", obs_ids=()):
    """select: "auto" — монитор из obs_ids (захваты экрана в сцене OBS); "5120x2160" — по разрешению;
    иначе подстрока имени/ID монитора ("DISPLAY2", "MTT1337")."""
    s = str(select).strip().lower()
    if s == "auto":
        hits = [m for m in mons if m["id"] in obs_ids]
        why = t("в текущей сцене OBS нет включённого «Захвата экрана» подключённого монитора" if not hits else
                "в текущей сцене OBS несколько «Захватов экрана» — оставьте один или задайте display в config.yaml")
    elif re.fullmatch(r"\d+x\d+", s):
        hits = [m for m in mons if f"{m['width']}x{m['height']}" == s]
        why = t("монитор {} не найден" if not hits else "мониторов {} несколько — задайте display по имени").format(s)
    else:
        hits = [m for m in mons if s in (m["device"] + m["id"]).lower()]
        why = t("монитор «{}» не найден" if not hits else "под «{}» подходит несколько мониторов").format(select)
    if len(hits) == 1:
        return hits[0]
    found = "\n".join(f"  {m['device']} {m['width']}x{m['height']} @({m['left']},{m['top']}) dpi {m['dpi']} {m['id']}"
                      for m in mons)
    raise LookupError(t("Не могу выбрать дисплей: {}. Мониторы:\n{}").format(why, found))


def custom_sprite(c, size):
    """Свой курсор программы из лога: снимок в масштабе size к системному курсору при записи."""
    img = Image.open(io.BytesIO(base64.b64decode(c["png"]))).convert("RGBA")
    k = size / c["ref"]
    w, h = max(1, round(img.width * k)), max(1, round(img.height * k))
    return img.resize((w, h), Image.LANCZOS), (c["hot"][0] * w / img.width, c["hot"][1] * h / img.height)


# Свой набор курсоров: вид → регулярка по имени файла, как их называют паки курсоров для Windows
PACK_NAMES = {
    "help": r"help", "busy": r"work|appstart|background", "wait": r"busy|wait", "cross": r"cross|precision",
    "ibeam": r"beam|text", "no": r"unavail|nodrop|forbid| no ", "size_ns": r" ns |vert", "size_we": r" ew | we |horiz|horz",
    "size_nwse": r"nwse|di?a?go?n?a?l? ?1", "size_nesw": r"nesw|di?a?go?n?a?l? ?2", "size_all": r"move",
    "hand": r"link|hand(?!writ)",
}


def pack_files(path):
    """Набор для рендера из выбранного файла: он — стрелка, остальные виды — файлы .cur/.ani рядом, по имени.
    {вид: путь}; файла нет — {} (рендер возьмёт системные)."""
    p = Path(path)
    if not p.is_file():
        return {}
    out = {"arrow": str(p)}
    for f in sorted(p.parent.iterdir()):
        if f == p or f.suffix.lower() not in (".cur", ".ani"):
            continue
        stem = " " + re.sub(r"[^a-z0-9]+", " ", f.stem.lower()) + " "
        name = next((n for n, rx in PACK_NAMES.items() if re.search(rx, stem)), None)
        if name and name not in out:
            out[name] = str(f)
    return out


def cur_sprite(path, size):
    """Файл .cur без Windows (Pillow, .ani не умеет): самая крупная картинка в масштабе size и её hotspot."""
    data = Path(path).read_bytes()
    img = Image.open(path).convert("RGBA")  # Pillow берёт самую крупную картинку
    ents = [data[6 + 16 * i:22 + 16 * i] for i in range(int.from_bytes(data[4:6], "little"))]
    e = next(e for e in ents if (e[0] or 256) == img.width)
    k = size / img.width
    w, h = max(1, round(img.width * k)), max(1, round(img.height * k))
    return img.resize((w, h), Image.LANCZOS), (int.from_bytes(e[4:6], "little") * k, int.from_bytes(e[6:8], "little") * k)


def pack_snapshot(sprite, ref):
    """(RGBA, hotspot) → {png base64 в родном размере, hot, ref} для custom_sprite. ref — размер системного курсора
    на экране в тот момент, px."""
    buf = io.BytesIO()
    sprite[0].save(buf, "PNG")
    return {"png": base64.b64encode(buf.getvalue()).decode(), "hot": sprite[1], "ref": ref}


class BaseLogger:
    """Опрашивает курсор в отдельном потоке. Время — perf_counter_ns, координаты — относительно дисплея.
    Как читать курсор, решает наследник в _run."""

    def __init__(self, display, hz, types, ref):
        self.display, self.period = display, 1e9 / hz
        self.samples = []  # (t_ns, x, y, type_idx, showing)
        self.types, self.custom = list(types), {}  # + свои курсоры программ: имя → снимок (см. add_custom)
        self.ref = ref  # размер системного курсора на экране при записи
        self.clicks = []   # (t_ns, "L"/"R", down 1/0, x, y)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join()

    def wait_tick(self, nxt):
        """Спит до следующего опроса. Возвращает его время; отстали — не догоняем пачкой."""
        nxt += self.period
        delay = nxt - time.perf_counter_ns()
        if delay > 0:
            time.sleep(delay / 1e9)
            return nxt
        return time.perf_counter_ns()

    def add_custom(self, snap, seen):
        """Свой курсор программы: одинаковые картинки с разными хэндлами — один тип. 0 (стрелка), если снять не вышло."""
        if not snap:
            return 0
        key = snap["png"] + repr(snap["hot"])
        if key not in seen:
            name = f"custom{len(self.custom)}"
            self.custom[name] = snap
            self.types.append(name)
            seen[key] = len(self.types) - 1
        return seen[key]

    def to_dict(self, t0_ns, pauses=(), end_ns=None):
        """Лог для файла записи, начатого в t0_ns (и законченного в end_ns, если OBS разбил запись на файлы).
        pauses — [[начало, конец или None]] пауз записи, ns: на видео их нет, поэтому сэмплы из пауз выкидываем,
        а время после паузы сдвигаем на её длину."""
        pauses = [(a, b) for a, b in pauses if a >= t0_ns and (end_ns is None or a < end_ns)]

        def at(t):
            """(мс на видео, попал ли момент в паузу); момент внутри паузы — её начало, то есть склейка."""
            shift = 0
            for a, b in pauses:
                if t < a:
                    break
                if b is None or t < b:
                    return round((a - t0_ns - shift) / 1e6, 3), True
                shift += b - a
            return round((t - t0_ns - shift) / 1e6, 3), False

        lo, hi = t0_ns - 1e9, (end_ns or float("inf")) + 1e9  # с запасом в секунду по краям файла
        samples, clicks = [], []
        for t, x, y, k, v in self.samples:
            if lo <= t <= hi and not (m := at(t))[1]:
                samples.append([m[0], x, y, k, v])
        for t, b, d, x, y in self.clicks:
            if lo <= t <= hi and not ((m := at(t))[1] and d):  # нажатие в паузе на видео не попало, отпускание — на склейке
                clicks.append([m[0], b, d, x, y])
        return {"version": 1, "display": self.display, "hz": round(1e9 / self.period), "types": self.types,
                "custom": self.custom, "samples": samples, "clicks": clicks}
