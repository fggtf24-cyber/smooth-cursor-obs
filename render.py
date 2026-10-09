"""Рендер: ffmpeg накладывает PNG-спрайты курсора через overlay, позиции покадрово задаёт sendcmd.

Каждый спрайт (тип курсора × шаг анимации клика, «призраки» шлейфа, отладочный сырой курсор) — отдельный overlay.
Неактивные спрятаны за кадром (x = HIDE), поэтому смена типа — это просто перенос двух overlay.
"""
import csv
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
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
    r = 2 * max(img.size)  # холст с запасом, чтобы повёрнутый курсор не обрезался
    c = Image.new("RGBa", (2 * r, 2 * r))
    c.paste(img.convert("RGBa"), (round(r - hot[0]), round(r - hot[1])))
    n = round(2 * r * scale)
    c = c.rotate(deg, Image.BICUBIC, center=(r, r)).resize((n, n), Image.LANCZOS).convert("RGBA")
    box = c.getbbox()
    return c.crop(box), (n / 2 - box[0], n / 2 - box[1])


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
    return {"w": s["width"], "h": s["height"], "fps": num / den, "frames": int(s["nb_read_packets"]),
            "duration": float(j["format"]["duration"]), "pix_fmt": s.get("pix_fmt", "yuv420p"),
            # без тегов считаем как OBS по умолчанию: BT.709, ограниченный диапазон
            "space": tag(s, "color_space"), "range": "pc" if s.get("color_range") == "pc" else "tv",
            "trc": tag(s, "color_transfer"), "primaries": tag(s, "color_primaries")}


def tag(stream, key):
    v = stream.get(key, "unknown")
    return "bt709" if v in ("unknown", "reserved") else v


def make_sprites(work, types, clicked, size, rc):
    """PNG для каждого слота; возвращает [(слот, файл, hot_x, hot_y)] в порядке наложения (снизу вверх)."""
    slots, base = [], {t: wc.cursor_sprite(t, size) for t in types}
    if rc["motion_blur"]:
        for gi, a in enumerate(ghost_alphas(rc)):
            for t, (img, hot) in base.items():
                g = img.copy()
                g.putalpha(img.getchannel("A").point(lambda v: round(v * a)))
                slots.append((f"g{gi}_{t}", g, hot))
    for t, (img, hot) in base.items():
        slots.append((f"{t}_0", img, hot))
        if t in clicked:
            big, big_hot = wc.cursor_sprite(t, size * 4)  # поворот с 4× запасом и уменьшение — без мыла и лесенки
            for li in range(1, CLICK_STEPS):
                p = li / (CLICK_STEPS - 1)
                im, h = press_sprite(big, big_hot, (1 - (1 - rc["click_scale"]) * p) / 4, rc["click_tilt_deg"] * p)
                slots.append((f"{t}_{li}", im, h))
    if rc["debug_raw"]:
        img, hot = base.get("arrow") or wc.cursor_sprite("arrow", size)
        a = np.array(img, float)
        a[..., :3] = a[..., :3] * 0.3 + np.array([255, 30, 60]) * 0.7  # красный
        a[..., 3] *= 0.6
        slots.append(("raw", Image.fromarray(a.round().astype(np.uint8), "RGBA"), hot))
    out = []
    for name, im, hot in slots:
        im.save(work / f"{name}.png")
        out.append((name, f"{name}.png", *hot))
    return out


def write_commands(path, track, info, types, slots, clip):
    """sendcmd: на каждом кадре двигаем только изменившиеся overlay. Координаты — уже в пикселях видео
    (vx, vy, vghosts, raw_vx). clip = (начало, конец) в секундах."""
    hot = {name: (hx, hy) for name, _, hx, hy in slots}
    fps, (t0, t1) = info["fps"], clip
    n_ghosts = len(track["vghosts"])
    state = {name: None for name in hot}
    with open(path, "w", encoding="ascii") as f:
        for n in range(len(track["x"])):
            if not t0 - 1 / fps <= n / fps <= t1 + 1 / fps:
                continue
            want = {}
            t = types[track["type"][n]] if track["type"][n] < len(types) else "arrow"
            t = t if f"{t}_0" in hot else "arrow"
            if track["visible"][n]:
                x, y, lvl = track["vx"][n], track["vy"][n], track["level"][n]
                main = f"{t}_{lvl}"
                want[main if main in hot else f"{t}_0"] = (x, y)
                # Шлейф только в движении и не во время анимации клика: несжатые «призраки» торчали бы из-под
                # наклонённого курсора, а в покое утолщали бы его края.
                for gi, (gx, gy) in enumerate(track["vghosts"]):
                    if lvl == 0 and abs(gx[n] - x) + abs(gy[n] - y) > 0.5:
                        want[f"g{n_ghosts - 1 - gi}_{t}"] = (gx[n], gy[n])
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
            if cmds:
                # ffmpeg отсчитывает время кадров от начала файла (или от -ss). Окно в 1 с: команда сработает
                # и при выпавших кадрах, а sendcmd не будет перебирать старые интервалы.
                ts = max(0.0, (n - 0.5) / fps - t0)
                f.write(f"{ts:.6f}-{ts + 1:.6f} {', '.join(cmds)};\n")


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

    ghost_ms = [1000 / fps * rc["blur_length"] * (i + 1) / len(ghost_alphas(rc))
                for i in range(len(ghost_alphas(rc)))] if rc["motion_blur"] else []
    track = smoothing.frame_track(lg, fps, info["frames"], sm, offset, ghost_ms)
    press = click_curve(track["click_age"], rc["click_ms"], track["press_len"]) if rc["click_animation"] else np.zeros(len(track["x"]))
    track["level"] = np.round(press * (CLICK_STEPS - 1)).astype(int)
    cl, ct, cr, cb = mp["crop"]  # за кропом источника курсора на видео нет
    track["visible"] &= (track["x"] >= cl) & (track["x"] < cr) & (track["y"] >= ct) & (track["y"] < cb)
    track["vx"], track["vy"] = to_video(track["x"], track["y"])
    track["raw_vx"], track["raw_vy"] = to_video(track["raw_x"], track["raw_y"])
    track["vghosts"] = [to_video(gx, gy) for gx, gy in track["ghosts"]]
    types = lg["types"]
    used = sorted({types[k] for k in np.unique(track["type"][track["visible"]])} | {"arrow"}, key=types.index)
    clicked = {types[k] for k in np.unique(track["type"][track["visible"] & (track["level"] > 0)])}
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
        slots = make_sprites(work, used, clicked, size, rc)
        write_commands(work / "cmds.txt", track, info, types, slots, span)
        # Накладываем прямо в YUV исходника: через RGB (format=auto) цвета видео чуть съезжают
        pf = info["pix_fmt"]
        sub = "444" if "444" in pf else "422" if "422" in pf else "420"
        deep = "10" in pf or "12" in pf or "16" in pf
        chain = [f"[0:v]format=yuv{sub}p{'10le' if deep else ''},sendcmd=f=cmds.txt[v0]"]
        for i, (name, *_) in enumerate(slots, 1):
            chain.append(f"[v{i - 1}][{i}:v]overlay@{name}=x={HIDE}:y=0:format=yuv{sub}{'p10' if deep else ''}"
                         f":eof_action=repeat[v{i}]")
        (work / "graph.txt").write_text(";".join(chain), encoding="ascii")
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-hwaccel", "auto"]
        if clip:
            cmd += ["-ss", f"{clip[0]:.3f}", "-t", f"{clip[1]:.3f}"]
        cmd += ["-i", str(video)]
        for _, png, *_ in slots:
            cmd += ["-i", png]
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
                                 creationflags=CREATE_NO_WINDOW)
            try:
                for line in p.stdout:
                    if line.startswith("out_time_us=") and line[12:].strip().isdigit():
                        if progress(int(line[12:]) / 1e4 / duration):
                            raise RuntimeError(t("рендер отменён"))
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
