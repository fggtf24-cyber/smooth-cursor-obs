"""Рендер: ffmpeg накладывает PNG-спрайты курсора через overlay, позиции покадрово задаёт sendcmd.

Каждый спрайт (тип курсора × шаг анимации клика, «призраки» шлейфа, отладочный сырой курсор) — отдельный overlay.
Неактивные спрятаны за кадром (x = HIDE), поэтому смена типа — это просто перенос двух overlay.
"""
import csv
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image

import smoothing
import win32cursor as wc
from i18n import t

log = logging.getLogger("smooth")
HIDE = -10000
CLICK_STEPS = 16  # шагов анимации клика (0 — обычный курсор)
TILT_STEP_DEG = 1.0  # шаг угла наклона в движении: поворот плавный, ступенек не видно, а спрайтов немного
TILT_SPEED = 0.6  # ширин кадра в секунду по горизонтали: на этой скорости поворот — 3/4 от полного
TILT_SMOOTH_MS = 70  # сглаживание угла по времени: поворачивается плавно, без рывков от толчков руки
TILT_RAMP_MS = 150  # после анимации клика наклон возвращается за это время, а не скачком


def click_curve(age_ms, duration_ms, press_ms=0):
    """Сила анимации клика 0..1..0: за первую четверть длительности вжимается (ease-out), держится, пока кнопка
    зажата (press_ms — сколько её держали, inf — ещё держат), потом за оставшиеся 3/4 плавно отпускает."""
    attack, release = 0.25 * duration_ms, 0.75 * duration_ms
    hold = np.maximum(0, np.asarray(press_ms, float) - attack)
    age = np.asarray(age_ms, float)
    with np.errstate(invalid="ignore"):  # inf − inf у «давно отпущенных» → nan → считаем отпущенными
        v = np.nan_to_num(np.clip((age - attack - hold) / release, 0, 1), nan=1.0)
    rise = 1 - (1 - np.clip(age / attack, 0, 1)) ** 3  # до первого клика age < 0 → 0
    return np.where(age < attack, rise, np.where(age < attack + hold, 1.0, 1 - v * v * (3 - 2 * v)))


def press_sprite(img, hot, scale, deg):
    """Сжатие и поворот против часовой вокруг hotspot: точка клика остаётся на месте. Возвращает (картинка, hotspot)."""
    x0, y0, x1, y1 = img.getbbox() or (0, 0, *img.size)
    # холст — круг до дальнего угла курсора: повёрнутый не обрежется, а лишних пикселей не крутим (это дорого).
    # Кратно 4: при уменьшении 4× спрайта пиксели ложатся на сетку обычного курсора, а не между ними (мыло).
    r = 4 * math.ceil((max(math.hypot(x - hot[0], y - hot[1]) for x in (x0, x1) for y in (y0, y1)) + 2) / 4)
    c = Image.new("RGBa", (2 * r, 2 * r))
    c.paste(img.convert("RGBa"), (round(r - hot[0]), round(r - hot[1])))
    n = round(2 * r * scale)
    c = c.rotate(deg, Image.BICUBIC, center=(r, r)).resize((n, n), Image.LANCZOS).convert("RGBA")
    box = c.getbbox()
    return c.crop(box), (n / 2 - box[0], n / 2 - box[1])


def pose_sprite(big, big_hot, rc, lvl, tilt):
    """Курсор из 4× спрайта: шаг анимации клика lvl и наклон в движении tilt (в шагах TILT_STEP_DEG).
    Возвращает (картинка, hotspot)."""
    p = lvl / (CLICK_STEPS - 1)
    return press_sprite(big, big_hot, (1 - (1 - rc["click_scale"]) * p) / 4,
                        rc["click_tilt_deg"] * p + tilt * TILT_STEP_DEG)


def tilt_angle(v, right_deg, left_deg, since_click_ms=np.inf):
    """Угол наклона в движении, ° (плюс — против часовой), по скорости v (ширин кадра в секунду по горизонтали, плюс —
    вправо). В покое курсор как в системе; на ходу поворачивается от этого положения в сторону движения: вправо — на
    right_deg, влево — на left_deg (у стрелки остриё скошено на 22.5°: при повороте на 22° вправо она смотрит прямо
    вверх). Минус — назад, как от инерции: едет вправо — поворот влево. Поворот растёт со скоростью: медленно — чуть-чуть,
    быстро — полный. Во время анимации клика (since_click_ms = 0) наклона нет: клик сам поворачивает курсор, а пары
    «шаг клика × шаг наклона» дали бы сотни спрайтов и рендер в 2–3 раза дольше. После неё наклон плавно возвращается."""
    f = np.tanh(np.asarray(v) / TILT_SPEED) * np.clip(np.asarray(since_click_ms, float) / TILT_RAMP_MS, 0, 1)
    return -f * np.where(f > 0, right_deg, left_deg)  # f: −1…1 — насколько полно повернуть и в какую сторону


def smooth_tilt(deg, fps):
    """Угол по кадрам, сглаженный по времени гауссом (σ = TILT_SMOOTH_MS): без запаздывания, как траектория."""
    s = TILT_SMOOTH_MS * fps / 1000
    h = math.ceil(3 * s)
    k = np.exp(-0.5 * (np.arange(-h, h + 1) / s) ** 2)
    return np.convolve(deg, k / k.sum())[h:h + len(deg)]


def ghost_tilt(k):
    """Шаг наклона «призраков» шлейфа — каждый четвёртый: они бледные и сдвинуты назад, разницы в пару градусов не
    видно, а слоёв в ffmpeg вчетверо меньше (каждый слой — время на каждом кадре; при наклоне 72/27 рендер в 1.3×
    быстрее, чем с шагом 2)."""
    return int(np.round(k / 4)) * 4


def tilt_tag(k):
    """Суффикс имени спрайта с наклоном: _m3 — по часовой на 3 шага, _p2 — против часовой."""
    return f"_{'p' if k > 0 else 'm'}{abs(k)}" if k else ""


def ghost_alphas(rc):
    """Непрозрачность «призраков» шлейфа от дальнего к ближнему; чем длиннее шлейф, тем их больше."""
    n = int(np.clip(round(12 * rc["blur_length"]), 2, 16))
    return [min(1.0, 0.45 * rc["blur_opacity"] * (i + 1) / n) for i in range(n)]


APP_DIR = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW


FFMPEG_HOME = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SmoothCursor" / "ffmpeg"  # сюда ставит программа
FFMPEG_URLS = ("https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
               "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip")


def tool(ffmpeg, name):
    """ffmpeg/ffprobe: путь из настроек; «auto» — рядом с программой, установленный программой, PATH, C:/ffmpeg."""
    cands = [] if ffmpeg in ("", "auto") else [Path(ffmpeg).with_name(name + ".exe")]
    cands += [APP_DIR / "ffmpeg" / f"{name}.exe", FFMPEG_HOME / f"{name}.exe", shutil.which(name),
              Path("C:/ffmpeg/bin") / f"{name}.exe"]
    found = next((str(c) for c in cands if c and Path(c).is_file()), None)
    if not found:
        raise FileNotFoundError(t("{} не найден ({}) — укажите путь к ffmpeg в настройках").format(name, ffmpeg))
    return found


def install_ffmpeg(progress=lambda pct: None):
    """Скачивает сборку ffmpeg и кладёт ffmpeg.exe/ffprobe.exe в FFMPEG_HOME. progress(pct) → True отменяет."""
    import urllib.request
    import zipfile
    FFMPEG_HOME.mkdir(parents=True, exist_ok=True)
    tmp = FFMPEG_HOME / "download.zip"
    err = None
    for url in FFMPEG_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "SmoothCursor"})
            with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
                total, done = int(r.headers.get("Content-Length") or 0), 0
                while chunk := r.read(1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    if progress(done * 100 / total if total else 0):
                        raise RuntimeError(t("загрузка отменена"))
            with zipfile.ZipFile(tmp) as z:
                for member in z.namelist():
                    if member.endswith(("/ffmpeg.exe", "/ffprobe.exe")):
                        (FFMPEG_HOME / Path(member).name).write_bytes(z.read(member))
            break
        except RuntimeError:
            raise
        except Exception as e:
            err = e
    tmp.unlink(missing_ok=True)
    if not (FFMPEG_HOME / "ffmpeg.exe").exists():
        raise RuntimeError(t("не удалось скачать ffmpeg: {}").format(err))
    _working.clear()
    return str(FFMPEG_HOME / "ffmpeg.exe")


# Кодировщики по порядку предпочтения: NVIDIA, AMD, Intel, затем процессор (работает везде, но медленнее)
ENCODERS = ("hevc_nvenc", "hevc_amf", "hevc_qsv", "libx264")
_working = {}


def encoder_works(ffmpeg, codec):
    """Пробный кадр: кодировщик может быть в сборке ffmpeg, но без подходящей видеокарты не работать."""
    if (ffmpeg, codec) not in _working:
        r = subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=s=640x360:d=0.1", "-frames:v", "1",
                            "-c:v", codec, "-f", "null", "-"], capture_output=True, creationflags=CREATE_NO_WINDOW)
        _working[(ffmpeg, codec)] = r.returncode == 0
    return _working[(ffmpeg, codec)]


def pick_encoder(ffmpeg, codec):
    if codec != "auto" and encoder_works(ffmpeg, codec):
        return codec
    return next(c for c in ENCODERS if c == "libx264" or encoder_works(ffmpeg, c))


def encoder_args(codec, cq, preset):
    """Качество одним числом (меньше — лучше) и пресет p1…p7 → параметры конкретного кодировщика."""
    p = int(str(preset).lstrip("p") or 5)
    if codec.endswith("_nvenc"):
        return ["-preset", f"p{p}", "-tune", "hq", "-rc", "vbr", "-cq", str(cq), "-b:v", "0"]
    if codec.endswith("_amf"):
        return ["-quality", ("speed", "balanced", "quality")[min(2, (p - 1) // 3)], "-rc", "cqp",
                "-qp_i", str(cq), "-qp_p", str(cq + 2), "-qp_b", str(cq + 4)]
    if codec.endswith("_qsv"):
        return ["-preset", ("veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")[p - 1],
                "-global_quality", str(cq)]
    return ["-preset", ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow")[p - 1],
            "-crf", str(cq), "-pix_fmt", "yuv420p"]


def probe(ffprobe, video):
    r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                        "stream=width,height,r_frame_rate,nb_read_packets,pix_fmt,color_space,color_range,"
                        "color_transfer,color_primaries:format=duration",
                        "-of", "json", str(video)], capture_output=True, text=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode:
        raise RuntimeError(t("ffprobe не смог прочитать {}: {}").format(video, r.stderr.strip()))
    j = json.loads(r.stdout)
    s = j["streams"][0]
    num, den = map(int, s["r_frame_rate"].split("/"))
    return {"w": s["width"], "h": s["height"], "fps": num / den, "rate": s["r_frame_rate"],
            "frames": int(s["nb_read_packets"]),
            "duration": float(j["format"]["duration"]), "pix_fmt": s.get("pix_fmt", "yuv420p"),
            # без тегов считаем как OBS по умолчанию: BT.709, ограниченный диапазон
            "space": tag(s, "color_space"), "range": "pc" if s.get("color_range") == "pc" else "tv",
            "trc": tag(s, "color_transfer"), "primaries": tag(s, "color_primaries")}


def tag(stream, key):
    v = stream.get(key, "unknown")
    return "bt709" if v in ("unknown", "reserved") else v


def make_sprites(work, types, poses, size, rc):
    """PNG для каждого слота; возвращает [(слот, файл, hot_x, hot_y)] в порядке наложения (снизу вверх).
    poses — {(тип, шаг клика, шаг наклона)}, которые есть в видео: спрайты только для них."""
    big = {}

    def pose(t, lvl, k):
        if not lvl and not k:
            return wc.cursor_sprite(t, size)
        if t not in big:  # поворот с 4× запасом и уменьшение — без мыла и лесенки
            big[t] = wc.cursor_sprite(t, size * 4)
        return pose_sprite(*big[t], rc, lvl, k)

    mains = {p: pose(*p) for p in sorted({(t, 0, 0) for t in types} | set(poses))}
    slots = []
    if rc["motion_blur"]:  # «призраки» — без анимации клика (при ней их нет), наклонены почти как курсор
        ghosts = {(t, gk): mains.get((t, 0, gk)) or pose(t, 0, gk)
                  for t, gk in sorted({(t, ghost_tilt(k)) for t, lvl, k in mains if not lvl})}
        for gi, a in enumerate(ghost_alphas(rc)):
            for (t, gk), (img, hot) in ghosts.items():
                g = img.copy()
                g.putalpha(img.getchannel("A").point(lambda v: round(v * a)))
                slots.append((f"g{gi}_{t}{tilt_tag(gk)}", g, hot))
    slots += [(f"{t}_{lvl}{tilt_tag(k)}", img, hot) for (t, lvl, k), (img, hot) in mains.items()]
    if rc["debug_raw"]:
        img, hot = mains.get(("arrow", 0, 0)) or wc.cursor_sprite("arrow", size)
        a = np.array(img, float)
        a[..., :3] = a[..., :3] * 0.3 + np.array([255, 30, 60]) * 0.7  # красный
        a[..., 3] *= 0.6
        slots.append(("raw", Image.fromarray(a.round().astype(np.uint8), "RGBA"), hot))
    out = []
    for name, im, hot in slots:
        im.save(work / f"{name}.png")
        out.append((name, f"{name}.png", *hot))
    return out


def sprite_name(track, n, types, have):
    """Тип курсора в кадре n и его спрайт с учётом шагов анимации клика и наклона (нет такого — обычный курсор)."""
    t = types[track["type"][n]] if track["type"][n] < len(types) else "arrow"
    t = t if f"{t}_0" in have else "arrow"
    main = f"{t}_{track['level'][n]}{tilt_tag(track['tilt'][n])}"
    return t, main if main in have else f"{t}_0"


BLUR_LAG = 4  # на сколько кадров вперёд ffmpeg может заглянуть, пока ждёт слой точного блюра (с запасом)


def piecewise(vals, n0, fps, t0):
    """Выражение ffmpeg от времени кадра t: значение кадра n0+i — до середины между ним и следующим кадром."""
    e = str(vals[-1])
    for i in range(len(vals) - 2, -1, -1):
        if vals[i] != vals[i + 1]:
            e = f"if(lt(t,{(n0 + i + 0.5) / fps - t0:.6f}),{vals[i]},{e})"
    return e


def write_commands(path, track, info, types, slots, clip, blur=None):
    """sendcmd: на каждом кадре двигаем только изменившиеся overlay. Координаты — уже в пикселях видео
    (vx, vy, vghosts, raw_vx). clip = (начало, конец) в секундах. blur — {кадр: левый верхний угол} слоя
    точного motion blur: тогда курсор рисует он, а не спрайты со шлейфом из копий."""
    hot = {name: (hx, hy) for name, _, hx, hy in slots}
    fps, (t0, t1) = info["fps"], clip
    n_ghosts = len(track["vghosts"])
    state = {name: None for name in hot}
    pos = lambda k: blur.get(k, (HIDE, 0))
    with open(path, "w", encoding="ascii") as f:
        for n in range(len(track["x"])):
            if not t0 - 1 / fps <= n / fps <= t1 + 1 / fps:
                continue
            want = {}
            t, main = sprite_name(track, n, types, hot)
            if blur is not None:
                pass  # слой точного блюра двигаем ниже — отдельными командами
            elif track["visible"][n]:
                x, y, lvl = track["vx"][n], track["vy"][n], track["level"][n]
                want[main] = (x, y)
                # Шлейф только в движении и не во время анимации клика: несжатые «призраки» торчали бы из-под
                # наклонённого курсора, а в покое утолщали бы его края.
                for gi, (gx, gy) in enumerate(track["vghosts"]):
                    if lvl == 0 and abs(gx[n] - x) + abs(gy[n] - y) > 0.5:
                        want[f"g{n_ghosts - 1 - gi}_{t}{tilt_tag(ghost_tilt(track['tilt'][n]))}"] = (gx[n], gy[n])
            if "raw" in hot and 0 <= track["raw_x"][n] < info["dw"] and 0 <= track["raw_y"][n] < info["dh"]:
                want["raw"] = (track["raw_vx"][n], track["raw_vy"][n])
            cmds = []
            for name, cur in state.items():
                new = None
                if name in want:
                    x, y = want[name]
                    new = (round(x - hot[name][0]), round(y - hot[name][1]))
                if new != cur:
                    cmds += [f"overlay@{name} x {new[0]}", f"overlay@{name} y {new[1]}"] if new else [f"overlay@{name} x {HIDE}"]
                    state[name] = new
            if blur is not None and pos(n) != pos(n - 1):
                # Вход слоя — поток, и ffmpeg, прежде чем смешать кадр, заглядывает в следующий: команда кадра n
                # срабатывает до того, как смешаны прошлые. Поэтому позиция — функция времени кадра t с запасом
                # на BLUR_LAG кадров назад: каждый кадр берёт свою, когда бы команда ни сработала.
                for axis in (0, 1):
                    vals = [pos(k)[axis] for k in range(n - BLUR_LAG, n + 1)]
                    cmds.append(f"overlay@blur {'xy'[axis]} '{piecewise(vals, n - BLUR_LAG, fps, t0)}'")
            if cmds:
                # ffmpeg отсчитывает время кадров от начала файла (или от -ss). Окно в 1 с: команда сработает
                # и при выпавших кадрах, а sendcmd не будет перебирать старые интервалы.
                ts = max(0.0, (n - 0.5) / fps - t0)
                f.write(f"{ts:.6f}-{ts + 1:.6f} {', '.join(cmds)};\n")


BLUR_CAP, BLUR_SAMPLES = 512, 256  # точный motion blur: слой не больше 512 px, до 256 выборок за выдержку


def blur_plan(track, sprites, types, fps, rc, grid, f0, f1):
    """Точный motion blur, как у камеры: курсор усреднён по всем моментам выдержки [t − 1/shutter с, t]
    с шагом ≈ 1 px пути, а не нарисован отдельными копиями. Каждый кадр курсора — готовый слой RGBA.

    sprites: {имя: (RGBA float32, hotspot)}; grid: (время мс, x, y, виден) траектории с шагом 1 мс в пикселях
    видео; f0…f1 — нужные кадры. Возвращает (ширина, высота слоя, {кадр: левый верхний угол}, байты слоёв).
    Хвост длиннее BLUR_CAP обрезается: при такой скорости он почти прозрачный."""
    tg, gx, gy, gv = grid
    S, tf = 1000 / rc["shutter"], track["t"]  # выдержка в мс: 1/60 с → 16.7
    prem = {}  # спрайты с премультиплицированной альфой: их можно просто складывать
    for name, (img, hot) in sprites.items():
        a = img[..., 3:] / 255
        prem[name] = (np.concatenate([img[..., :3] * a, a], -1).astype(np.float32), hot)
    sw = max(p.shape[1] for p, _ in prem.values()) + 2
    sh = max(p.shape[0] for p, _ in prem.values()) + 2
    m1 = min(f1, len(tf))  # дальше конца видео — пустые слои (запас, чтобы конец задавало видео)
    # длина пути за выдержку: по ней число выборок и размер слоя (по 99.5% кадров, чтобы один рывок не раздул все)
    probe = tf[f0:m1, None] - S * np.linspace(0, 1, 9)
    px, py = np.interp(probe, tg, gx), np.interp(probe, tg, gy)
    k = np.clip(np.ceil(np.hypot(np.diff(px), np.diff(py)).sum(1)), 1, BLUR_SAMPLES).astype(int)
    vis = track["visible"][f0:m1]
    pw, ph = sw, sh
    if vis.any():
        pw = int(np.clip(np.percentile(np.ptp(px[vis], 1), 99.5) + sw, sw, BLUR_CAP))
        ph = int(np.clip(np.percentile(np.ptp(py[vis], 1), 99.5) + sh, sh, BLUR_CAP))

    def samples(f):
        """Левые верхние углы спрайта во все моменты выдержки кадра f, где курсор был виден (первый — сейчас)."""
        p, (hx, hy) = prem[names[f]]
        ts = tf[f] - S * np.arange(k[f - f0]) / k[f - f0]
        keep = gv[np.clip(np.round((ts - tg[0]) / smoothing.STEP_MS).astype(int), 0, len(tg) - 1)]
        return (np.round(np.interp(ts[keep], tg, gx) - hx).astype(int),
                np.round(np.interp(ts[keep], tg, gy) - hy).astype(int))

    names, origin = {}, {}
    for f in range(f0, m1):
        if track["visible"][f]:
            names[f] = sprite_name(track, f, types, prem)[1]
            xs, ys = samples(f)
            if not xs.size:  # в момент кадра курсор виден, так что сюда не попадаем — но без падения
                del names[f]
                continue
            h, w = prem[names[f]][0].shape[:2]
            # слой целиком вмещает след; если не вмещает — ведущий (нынешний) курсор целый, обрезается хвост
            origin[f] = (int(min(max(xs.min(), xs[0] + w - pw), xs[0])),
                         int(min(max(ys.min(), ys[0] + h - ph), ys[0])))

    def layers():
        empty, still = bytes(pw * ph * 4), {}
        for f in range(f0, f1):
            if f not in names:
                yield empty
                continue
            p, _ = prem[names[f]]
            h, w = p.shape[:2]
            xs, ys = samples(f)
            xs, ys = xs - origin[f][0], ys - origin[f][1]
            static = len(xs) == k[f - f0] and not (xs.any() or ys.any())  # курсор стоит — просто спрайт
            if static and names[f] in still:
                yield still[names[f]]
                continue
            bx0, by0 = max(xs.min(), 0), max(ys.min(), 0)
            bx1, by1 = min(xs.max() + w, pw), min(ys.max() + h, ph)
            acc = np.zeros((by1 - by0, bx1 - bx0, 4), np.float32)
            pos, cnt = np.unique(np.stack([xs, ys], 1), axis=0, return_counts=True)
            for (x, y), c in zip(pos.tolist(), cnt.tolist()):
                x0, y0, x1, y1 = max(x, bx0), max(y, by0), min(x + w, bx1), min(y + h, by1)
                if x0 < x1 and y0 < y1:
                    acc[y0 - by0:y1 - by0, x0 - bx0:x1 - bx0] += p[y0 - y:y1 - y, x0 - x:x1 - x] * c
            out = np.zeros((ph, pw, 4), np.uint8)
            a = acc[..., 3]
            out[by0:by1, bx0:bx1, :3] = np.clip(acc[..., :3] / np.maximum(a, 1e-6)[..., None] + 0.5, 0, 255)
            out[by0:by1, bx0:bx1, 3] = np.clip(a * (255 / k[f - f0]) + 0.5, 0, 255)  # доля выдержки, когда виден
            buf = out.tobytes()
            if static:
                still[names[f]] = buf
            yield buf

    return pw, ph, origin, layers()


def export_csv(path, track, w, h, fps, types):
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["frame", "time_s", "x", "y", "visible", "type", "fusion_x", "fusion_y"])
        for n in range(len(track["x"])):
            x, y = track["vx"][n], track["vy"][n]
            wr.writerow([n, round(n / fps, 5), round(x, 2), round(y, 2), int(track["visible"][n]),
                         types[track["type"][n]], round(x / w, 6), round(1 - y / h, 6)])


def render(video, log_path, cfg, progress=None, clip=None):
    """Рендер видео с плавным курсором. progress(pct) → True отменяет рендер (по умолчанию — лог каждые 10%).
    clip = (начало_с, длина_с) — короткое превью во временный файл."""
    if progress is None:
        nxt = [0]

        def progress(pct):
            if pct >= nxt[0]:
                log.info(t("Рендер %d%%"), min(pct, 100))
                nxt[0] = pct // 10 * 10 + 10

    rc, sm = cfg["render"], cfg["smoothing"]
    ffmpeg, ffprobe = tool(rc["ffmpeg"], "ffmpeg"), tool(rc["ffmpeg"], "ffprobe")
    codec = pick_encoder(ffmpeg, rc["codec"])
    info = probe(ffprobe, video)
    lg = json.loads(Path(log_path).read_text(encoding="utf-8"))
    disp = lg["display"]
    info["dw"], info["dh"] = disp["width"], disp["height"]
    # Где дисплей на холсте OBS (снято с трансформации источника при записи); у старых логов — на весь кадр
    mp = disp.get("map") or {"m": [info["w"] / disp["width"], 0, 0, 0, info["h"] / disp["height"], 0],
                             "canvas": [info["w"], info["h"]], "crop": [0, 0, disp["width"], disp["height"]]}
    a, b, c, d, e, f = mp["m"]
    kx, ky = info["w"] / mp["canvas"][0], info["h"] / mp["canvas"][1]  # холст → видео (масштаб вывода OBS)

    def to_video(x, y):
        return kx * (a * x + b * y + c), ky * (d * x + e * y + f)

    fps = info["fps"]
    offset = cfg["sync"]["offset_ms"]
    if isinstance(offset, dict):  # {fps: мс, default: мс}
        offset = offset.get(round(fps), offset.get("default", 0))
    if clip:
        start = min(max(0.0, clip[0]), max(0.0, info["duration"] - 0.5))
        clip = (start, min(clip[1], info["duration"] - start))
    span = (clip[0], clip[0] + clip[1]) if clip else (0.0, float("inf"))
    duration = clip[1] if clip else info["duration"]
    log.info(t("%s %s: %dx%d @ %.3g fps, %s, offset %g мс, кодек %s"), t("Превью" if clip else "Рендер"), video.name,
             info["w"], info["h"], fps, t("{}–{} с").format(f"{clip[0]:.1f}", f"{span[1]:.1f}") if clip
             else t("{} кадров").format(info["frames"]), offset, codec)

    accurate = rc["motion_blur"] and rc["blur_accurate"]  # точный motion blur вместо шлейфа из копий
    ghost_ms = [1000 / fps * rc["blur_length"] * (i + 1) / len(ghost_alphas(rc))
                for i in range(len(ghost_alphas(rc)))] if rc["motion_blur"] and not accurate else []
    track = smoothing.frame_track(lg, fps, info["frames"], sm, offset, ghost_ms)
    press = click_curve(track["click_age"], rc["click_ms"], track["press_len"]) if rc["click_animation"] else np.zeros(len(track["x"]))
    track["level"] = np.round(press * (CLICK_STEPS - 1)).astype(int)
    cl, ct, cr, cb = mp["crop"]  # за кропом источника курсора на видео нет
    track["visible"] &= (track["x"] >= cl) & (track["x"] < cr) & (track["y"] >= ct) & (track["y"] < cb)
    track["vx"], track["vy"] = to_video(track["x"], track["y"])
    track["raw_vx"], track["raw_vy"] = to_video(track["raw_x"], track["raw_y"])
    track["vghosts"] = [to_video(gx, gy) for gx, gy in track["ghosts"]]
    vis = track["visible"]
    v = np.gradient(track["vx"]) * fps / info["w"] if len(vis) > 1 else np.zeros(len(vis))
    # там, где курсор появляется или пропадает, разница позиций — скачок, а не скорость
    ok = vis & np.r_[vis[1:], True] & np.r_[True, vis[:-1]]
    frames = np.arange(len(vis))
    since = (frames - np.maximum.accumulate(np.where(track["level"] > 0, frames, -len(vis)))) * 1000 / fps
    types = lg["types"]
    deg = smooth_tilt(np.where(ok, tilt_angle(v, rc["motion_tilt_right_deg"], rc["motion_tilt_left_deg"], since), 0.0), fps)
    track["tilt"] = np.where(vis & (track["level"] == 0), np.round(deg / TILT_STEP_DEG), 0).astype(int)
    used = sorted({types[k] for k in np.unique(track["type"][vis])} | {"arrow"}, key=types.index)
    poses = {(types[k], lvl, tl) for k, lvl, tl in zip(*(track[c][vis].tolist() for c in ("type", "level", "tilt")))}
    zoom = abs(kx * ky * (a * e - b * d)) ** 0.5  # во сколько раз захват увеличен на видео
    size = max(4, round(wc.cursor_base_size() * disp["dpi"] / 96 * rc["cursor_scale"] * zoom))
    if clip:
        out = Path(tempfile.gettempdir()) / f"{video.stem}_preview.mp4"
        try:
            out.unlink(missing_ok=True)
        except OSError:  # прошлое превью ещё открыто в плеере
            out = out.with_name(f"{video.stem}_preview_{time.strftime('%H%M%S')}.mp4")
    else:
        out = video.with_name(video.stem + ("_smooth_debug" if rc["debug_raw"] else "_smooth") + ".mp4")
        if rc["export_keyframes"]:
            export_csv(video.with_name(video.stem + "_cursor.csv"), track, info["w"], info["h"], fps, types)

    with tempfile.TemporaryDirectory(prefix="smooth_cursor_") as tmp:
        work = Path(tmp)
        slots = make_sprites(work, used, poses, size, {**rc, "motion_blur": rc["motion_blur"] and not accurate})
        blur = layers = None
        if accurate:  # курсор — один слой, кадры которого считаем здесь и подаём ffmpeg через stdin
            sprites = {name: (np.asarray(Image.open(work / png), np.float32), (hx, hy))
                       for name, png, hx, hy in slots if name != "raw"}
            slots = [("blur", None, 0, 0)] + [s for s in slots if s[0] == "raw"]
            f0 = int(np.ceil(span[0] * fps - 1e-6)) if clip else 0  # первый кадр превью
            f1 = (int(span[1] * fps) + 3 if clip else info["frames"]) + 2  # слоёв — с запасом: конец задаёт видео
            tg, sx, sy, gvis = track["path"]
            gv = gvis & (sx >= cl) & (sx < cr) & (sy >= ct) & (sy < cb)
            pw, ph, blur, layers = blur_plan(track, sprites, types, fps, rc, (tg, *to_video(sx, sy), gv), f0, f1)
            log.info(t("Точный motion blur: выдержка 1/%d с, слой %dx%d px"), rc["shutter"], pw, ph)
            blur_in = ["-f", "rawvideo", "-pixel_format", "rgba", "-video_size", f"{pw}x{ph}", "-framerate",
                       info["rate"], "-i", "pipe:0"]
            blur_off = (f0 - 0.5) / fps - span[0]
        write_commands(work / "cmds.txt", track, info, types, slots, span, blur)
        # Накладываем прямо в YUV исходника: через RGB (format=auto) цвета видео чуть съезжают
        pf = info["pix_fmt"]
        sub = "444" if "444" in pf else "422" if "422" in pf else "420"
        deep = "10" in pf or "12" in pf or "16" in pf
        chain = [f"[0:v]format=yuv{sub}p{'10le' if deep else ''},sendcmd=f=cmds.txt[v0]"]
        for i, (name, *_) in enumerate(slots, 1):
            inp = f"[{i}:v]"
            if name == "blur":
                # Слой кадра n — на полкадра раньше самого кадра (как окна sendcmd), чтобы кадр видео взял именно
                # его. Сдвиг — в микросекундах: в шкале самого слоя (1/fps) он округлился бы до целого кадра.
                chain.append(f"{inp}settb=AVTB,setpts=PTS+({blur_off:.6f})/TB[bl]")
                inp = "[bl]"
            chain.append(f"[v{i - 1}]{inp}overlay@{name}=x={HIDE}:y=0:format=yuv{sub}{'p10' if deep else ''}"
                         f":eof_action=repeat{':shortest=1' if name == 'blur' else ''}[v{i}]")  # видео кончилось — всё
        (work / "graph.txt").write_text(";".join(chain), encoding="ascii")
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-hwaccel", "auto"]
        if clip:
            cmd += ["-ss", f"{clip[0]:.3f}", "-t", f"{clip[1]:.3f}"]
        cmd += ["-i", str(video)]
        for name, png, *_ in slots:
            cmd += blur_in if name == "blur" else ["-i", png]
        cmd += ["-/filter_complex", "graph.txt", "-map", f"[v{len(slots)}]", "-map", "0:a?", "-c:a", "copy",
                "-c:v", codec, *encoder_args(codec, rc["cq"], rc["preset"]), "-fps_mode", "passthrough",
                "-colorspace", info["space"], "-color_range", info["range"],
                "-color_trc", info["trc"], "-color_primaries", info["primaries"]]
        if codec.startswith("hevc"):
            cmd += ["-tag:v", "hvc1"]
        part = out.with_name(out.stem + ".part.mp4")  # оборванный рендер не затрёт готовый и не выдаст себя за него
        cmd.append(str(part))
        with open(work / "ffmpeg.log", "w+", encoding="utf-8", errors="replace") as err:
            p = subprocess.Popen(cmd, cwd=work, stdout=subprocess.PIPE, stderr=err, text=True,
                                 stdin=subprocess.PIPE if layers else None, creationflags=CREATE_NO_WINDOW)
            failed = []
            if layers:
                def feed():  # в отдельном потоке: ffmpeg берёт слои по мере надобности, прогресс читаем здесь
                    try:
                        for buf in layers:
                            p.stdin.buffer.write(buf)
                        p.stdin.close()
                    except OSError:  # ffmpeg уже закрылся (отмена, ошибка) — причину скажет он сам
                        pass
                    except Exception as e:  # сбой расчёта слоя — нельзя молча доделать видео без курсора
                        failed.append(e)
                        p.kill()

                threading.Thread(target=feed, daemon=True).start()
            try:
                for line in p.stdout:
                    if line.startswith("out_time_us=") and line[12:].strip().isdigit():
                        if progress(int(line[12:]) / 1e4 / duration):
                            raise RuntimeError(t("рендер отменён"))
                if failed:
                    raise failed[0]
                if p.wait():
                    err.seek(0)
                    tail = "".join(err.readlines()[-15:])
                    raise RuntimeError(t("ffmpeg завершился с кодом {}:\n{}").format(p.returncode, tail))
            except BaseException:
                p.kill()
                p.wait()
                part.unlink(missing_ok=True)
                raise
    try:
        os.replace(part, out)
    except OSError:  # старый результат открыт в плеере — новый не выбрасываем
        raise RuntimeError(t("{} открыт в другой программе — новый рендер сохранён как {}").format(out.name, part.name))
    progress(100)

    o = probe(ffprobe, out)
    log.info(t("Готово: %s — %dx%d @ %.3g fps, %.2f с"), out, o["w"], o["h"], o["fps"], o["duration"])
    if (o["w"], o["h"]) != (info["w"], info["h"]) or abs(o["duration"] - duration) > 1.5 / fps:
        log.warning(t("Параметры результата не совпадают с исходником (%.2f с)!"), duration)
    return out
