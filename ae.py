"""After Effects: запись и курсор отдельными слоями в открытую композицию.

Курсор — картинка системного курсора «Smooth Cursor», привязанная к видео, ключи — прямо на ней; другие виды
курсора (текстовый, рука) привязаны к ней и повторяют её движение. Ключи — не на каждом кадре,
а только там, где без них кривая AE отошла бы от траектории дальше допуска: на остановках, кликах и изгибах пути.
Между ними — Bezier с касательными и скоростями из самого движения (влияние 1/3), как при ручной анимации: ключ можно
сдвинуть, и движение останется плавным. Скрипт .jsx отправляем в запущенный AE (AfterFX.exe -s).
"""
import glob
import json
import logging
import math
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np

import render
import win32cursor as wc
from i18n import t

log = logging.getLogger("smooth")
TOL_PX, TOL_DEG, TOL_PCT = 2.0, 1.0, 1.0  # допуск кривой AE: позиция (px на 1080p), поворот (°), масштаб (%)
TOL_MOVE = 0.25  # в движении позиции — ещё четверть пути за кадр: на ходу сдвиг в пару пикселей не виден, а ключей
# вдвое-втрое меньше; а в кадре клика ключ стоит всегда — там курсор точно на месте
SPRITE_X = 4  # курсор не меньше 4× размера (со Scale 25%), а в файле курсора есть крупнее — в его размере: в AE
# его можно увеличить без мыла
AE_VIDEO = (".mov", ".mp4", ".m4v")  # что AE открывает сам; остальное (mkv, flv, ts) — перепаковка в mp4 без пересжатия


def hermite(v0, v1, d0, d1, w, T):
    """Кривая AE между ключами со скоростями d0, d1 и влиянием 1/3: кубика Эрмита, w — доля отрезка 0..1."""
    return v0 + (d0 * T / 3) * 3 * (1 - w) ** 2 * w + (v1 - d1 * T / 3 - v0) * 3 * (1 - w) * w * w + (v1 - v0) * w ** 3


def path_curve(p0, p1, v0, v1, w, T):
    """Где AE покажет Position между ключами p0 → p1 (скорости v0, v1 в px/с): путь — Bezier с касательными v·T/3,
    по нему — с пройденным путём по графику скорости (|v0|, |v1|, влияние 1/3). None — график не монотонный
    (курсор пошёл бы назад): нужен ключ посередине."""
    c = np.array([p0, p0 + v0 * T / 3, p1 - v1 * T / 3, p1])
    u = np.linspace(0, 1, 97)[:, None]
    curve = (1 - u) ** 3 * c[0] + 3 * (1 - u) ** 2 * u * c[1] + 3 * (1 - u) * u * u * c[2] + u ** 3 * c[3]
    arc = np.r_[0, np.cumsum(np.hypot(*np.diff(curve, axis=0).T))]
    s0, s1, length = np.hypot(*v0), np.hypot(*v1), arc[-1]
    if length < 1e-6:
        return np.repeat(p0[None], len(w), 0)
    if (s0 + s1) * T / 3 > length * 1.001:
        return None
    d = hermite(0, length, s0, s1, w, T)
    return np.stack([np.interp(d, arc, curve[:, 0]), np.interp(d, arc, curve[:, 1])], 1)


def fit_keys(seeds, error, tol):
    """Кадры-ключи: seeds и ещё те, где ошибка кривой между соседними ключами больше tol (делим по худшему кадру),
    потом лишние убираем: ключ не нужен, если кривая и без него в допуске. error(a, b) → ошибки кадров a..b или
    None (такой отрезок нельзя)."""
    keys, todo = set(seeds), list(zip(seeds[:-1], seeds[1:]))
    while todo:
        a, b = todo.pop()
        if b - a < 2:
            continue
        e = error(a, b)
        if e is not None and e.max() <= tol:
            continue
        m = a + int(np.argmax(e)) if e is not None else (a + b) // 2
        m = m if a < m < b else (a + b) // 2
        keys.add(m)
        todo += [(a, m), (m, b)]
    ks, i = sorted(int(k) for k in keys), 1
    while i < len(ks) - 1:
        e = None if ks[i] in seeds else error(ks[i - 1], ks[i + 1])
        if e is not None and e.max() <= tol:
            del ks[i]
        else:
            i += 1
    return ks


def runs(mask):
    """[(первый, последний)] кадров каждого участка, где mask истинна."""
    d = np.diff(np.r_[0, mask.astype(int), 0])
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1))


def position_keys(x, y, vis, clicks, fps, px=TOL_PX):
    """Ключи Position на видимых участках: [(кадр, x, y, скорость px/с, касательная до, касательная после)].
    px — допуск в покое, px видео."""
    p = np.stack([x, y], 1)
    v = np.gradient(p, axis=0) * fps if len(p) > 1 else np.zeros_like(p)
    tol = px + TOL_MOVE * np.hypot(*v.T) / fps
    out = []
    for a, b in runs(vis):
        v[a], v[b] = (p[min(a + 1, b)] - p[a]) * fps, (p[b] - p[max(b - 1, a)]) * fps  # на краях — без чужих кадров

        def error(i, j):
            T = (j - i) / fps
            c = path_curve(p[i], p[j], v[i], v[j], np.arange(j - i + 1) / (j - i), T)
            return None if c is None else np.hypot(*(c - p[i:j + 1]).T) / tol[i:j + 1]

        ks = fit_keys(sorted({a, b} | {c for c in clicks if a <= c <= b}), error, 1.0)
        for k, n in enumerate(ks):
            back = (n - ks[k - 1]) / fps if k else 0
            ahead = (ks[k + 1] - n) / fps if k + 1 < len(ks) else 0
            out.append((n, *np.round(p[n], 3).tolist(), round(float(np.hypot(*v[n])), 3),
                        np.round(-v[n] * back / 3, 3).tolist(), np.round(v[n] * ahead / 3, 3).tolist()))
    return out


def value_keys(val, fps, tol, kinks=()):
    """Ключи числа (поворот, масштаб): [(кадр, значение, скорость до, скорость после)] в единицах/с; не меняется —
    один ключ. kinks — кадры перед кликами: там анимация клика срывается с места, ключ ломаный (скорость до — 0,
    после — сразу большая), иначе ломаный угол обсыпало бы ключами."""
    if np.ptp(val) < tol / 10:
        return [(0, float(val[0]), 0.0, 0.0)]
    d_in = np.gradient(val) * fps
    d_out = d_in.copy()
    k = np.array(sorted(n for n in kinks if 0 < n < len(val) - 1), int)
    d_in[k], d_out[k] = (val[k] - val[k - 1]) * fps, (val[k + 1] - val[k]) * fps

    def error(i, j):
        w = np.arange(j - i + 1) / (j - i)
        return np.abs(hermite(val[i], val[j], d_out[i], d_in[j], w, (j - i) / fps) - val[i:j + 1])

    return [(n, round(float(val[n]), 4), round(float(d_in[n]), 4), round(float(d_out[n]), 4))
            for n in fit_keys(sorted({0, len(val) - 1, *k.tolist()}), error, tol)]


def changes(v):
    """[(кадр, значение)] — только там, где значение меняется (ключи с удержанием)."""
    n = np.r_[0, np.flatnonzero(np.diff(v)) + 1]
    return [(int(i), int(v[i])) for i in n]


def hold_keys(on):
    """Opacity с удержанием: 100, где on, 0 — где нет; ключи только на сменах."""
    return [(n, 100 * x) for n, x in changes(on.astype(int))]


def recording_files(video):
    """Файлы, по которым запись узнать в композиции AE: исходник, перепакованный mp4 и готовые с курсором (их —
    подменить исходником). Возвращает (все, готовые)."""
    baked = [str(video.with_name(video.stem + e)) for e in ("_smooth.mp4", "_smooth_debug.mp4")]
    return [str(video), str(video.with_name(video.stem + "_ae") / (video.stem + ".mp4")), *baked], baked


def blur_settings(rc, fps):
    """Shutter Angle и Phase композиции AE, как размытие в программе: шлейф длиной blur_length кадра (точный —
    выдержка 1/shutter с) и целиком позади курсора."""
    angle = min(720.0, 360 * (fps / rc["shutter"] if rc["blur_accurate"] else rc["blur_length"]))
    return round(angle), -min(360, round(angle))  # фаза в AE — не дальше −360°


def export(video, log_path, cfg):
    """Слои для AE в папку «<запись>_ae» рядом с записью: курсоры PNG, при надобности видео в mp4, скрипт import.jsx.
    Возвращает (путь к скрипту, (Shutter Angle, Phase), число ключей Position)."""
    rc = cfg["render"]
    ffmpeg, ffprobe = render.tool(rc["ffmpeg"], "ffmpeg"), render.tool(rc["ffmpeg"], "ffprobe")
    info = render.probe(ffprobe, video)
    lg = json.loads(Path(log_path).read_text(encoding="utf-8"))
    track, _, _, size = render.cursor_track(lg, info, cfg)
    fps, vis, types = info["fps"], track["visible"], lg["types"]
    out = video.with_name(video.stem + "_ae")
    out.mkdir(exist_ok=True)
    src = video
    if video.suffix.lower() not in AE_VIDEO:
        src = out / (video.stem + ".mp4")
        if not src.exists():
            log.info(t("Перепаковываю %s в mp4 для After Effects…"), video.name)
            r = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-map", "0",
                                "-c", "copy", str(src)], capture_output=True, text=True,
                               creationflags=render.CREATE_NO_WINDOW)
            if r.returncode:
                src.unlink(missing_ok=True)
                raise RuntimeError(t("ffmpeg не смог перепаковать {}: {}").format(video.name, r.stderr.strip()[-300:]))

    sprites, kinds = [], []  # первым — самый частый вид курсора
    for k in np.argsort(-np.bincount(track["type"][vis], minlength=1), kind="stable"):
        if sprites and not (vis & (track["type"] == k)).any():
            break
        name = types[k] if k < len(types) else "arrow"
        px = max(size * SPRITE_X, wc.cursor_file_max(name))
        img, (hx, hy) = wc.cursor_sprite(name, px)
        png = out / f"cursor_{name}.png"
        img.save(png)
        sprites.append({"type": name, "png": str(png), "w": img.width, "h": img.height, "hx": hx, "hy": hy,
                        "scale": 100 * size / px})
        kinds.append(k)
    # один слой курсора: несколько видов — прекомпозиция, кадр i — вид i, hotspot у всех в точке box[0:2];
    # какой вид сейчас — Time Remap с удержанием (пока курсор не виден, вид прежний)
    kind = np.select([track["type"] == k for k in kinds], range(len(kinds)), 0)
    kind = kind[np.maximum.accumulate(np.where(vis, np.arange(len(vis)), 0))]  # вид последнего видимого кадра
    r = [sp["scale"] / sprites[0]["scale"] for sp in sprites]  # плотность пикселей относительно основного вида
    left, top = max(sp["hx"] * q for sp, q in zip(sprites, r)), max(sp["hy"] * q for sp, q in zip(sprites, r))
    box = [round(left) + 2, round(top) + 2,
           math.ceil(left + max((sp["w"] - sp["hx"]) * q for sp, q in zip(sprites, r))) + 4,
           math.ceil(top + max((sp["h"] - sp["hy"]) * q for sp, q in zip(sprites, r))) + 4]
    age = track["click_age"]  # мс с нажатия; до первого — меньше нуля
    clicks = set((np.flatnonzero((age[1:] >= 0) & ((age[:-1] < 0) | (np.diff(age) < 0))) + 1).tolist())
    pos = position_keys(track["vx"], track["vy"], vis, clicks, fps, TOL_PX * max(1.0, info["h"] / 1080))
    kinks = [c - 1 for c in clicks]
    # в AE поворот плюс — по часовой, у нас — против
    rot = value_keys(-(rc["click_tilt_deg"] * track["press"] + track["tilt_deg"]), fps, TOL_DEG, kinks)
    # масштаб подбираем от 100%, а на картинку ставим от её Scale (крупный PNG уменьшен): допуск — в процентах размера
    f = sprites[0]["scale"] / 100
    scale = [(n, round(v * f, 4), round(a * f, 4), round(b * f, 4))
             for n, v, a, b in value_keys(100 * (1 - (1 - rc["click_scale"]) * track["press"]), fps, TOL_PCT, kinks)]
    angle, phase = blur_settings(rc, fps)
    same, baked = recording_files(video)
    data = {"name": f"Smooth Cursor — {video.stem}", "video": str(src), "w": info["w"], "h": info["h"], "fps": fps,
            "same": same, "baked": baked, "frames": info["frames"], "sprites": sprites, "box": box,
            "show": hold_keys(vis), "kind": changes(kind), "blur": rc["motion_blur"],
            "has": t("У этой записи в композиции курсор уже есть. Чтобы заменить, удалите слой «Smooth Cursor» "
                     "и нажмите ещё раз."),
            "angle": angle, "phase": phase, "pos": pos, "rot": rot, "scl": scale}
    jsx = out / "import.jsx"
    jsx.write_text(JSX.replace("/*DATA*/", json.dumps(data)), encoding="utf-8-sig")  # с BOM — так AE читает UTF-8
    log.info(t("After Effects: %d ключей позиции на %d кадров, слои в %s"), len(pos), info["frames"], out)
    return jsx, (angle, phase), len(pos)


def afterfx():
    """AfterFX.exe: запущенный (чтобы скрипт ушёл в него, а не в другую версию), иначе новейший установленный."""
    running = wc.running_exe("AfterFX.exe")
    if running:
        return running
    found = sorted(glob.glob(os.path.expandvars(r"%ProgramFiles%\Adobe\Adobe After Effects *\Support Files\AfterFX.exe")))
    if not found:
        raise RuntimeError(t("After Effects не найден — установлен ли он?"))
    return found[-1]


def send(jsx):
    """Выполнить скрипт в After Effects: запущенный возьмёт его сам, иначе AE запустится."""
    # путь — экранами \uXXXX: в командной строке только ASCII, кириллица в папке записей не сломает запуск
    path = json.dumps(str(jsx))[1:-1].replace("'", "\\'")
    subprocess.Popen([afterfx(), "-s", f"$.evalFile(new File('{path}'))"], creationflags=render.CREATE_NO_WINDOW)


def dispatch(videos, selected):
    """Курсор — тем записям из списка, что уже лежат в открытой композиции AE (их import.jsx, подготовленный раньше:
    кнопкой или при отправке в Premiere), а нет таких — выбранной. Какие там записи, решает скрипт в AE: ответить
    программе AE не может без разрешения скриптам писать файлы. Запись в композиции без подготовленных слоёв —
    AE скажет, какую выбрать."""
    recs = []
    for v in videos if selected in videos else [*videos, selected]:
        jsx = v.with_name(v.stem + "_ae") / "import.jsx"
        recs.append({"name": v.stem, "same": recording_files(v)[0], "jsx": str(jsx) if jsx.exists() else None})
    script = DISPATCH.replace("/*RECS*/", json.dumps(recs)).replace("/*SEL*/", str([r["name"] for r in recs].index(selected.stem))).replace(
        "/*MSG*/", json.dumps(t("В композиции есть записи, которые Smooth Cursor ещё не готовил для After Effects. "
                                "Выберите их в программе и нажмите «→ After Effects» ещё раз:")))
    path = Path(tempfile.gettempdir()) / "smooth_cursor_ae.jsx"
    path.write_text(script, encoding="utf-8-sig")
    send(path)


DISPATCH = r"""(function () {
var R = /*RECS*/, sel = /*SEL*/, comp = app.project.activeItem, run = [], missing = [], seen = {}, i, k, j;
function file(L) {
    try { return L.source.file.fsName.toLowerCase(); } catch (e) { return ""; }
}
for (i = 1; comp instanceof CompItem && i <= comp.numLayers; i++) {
    var f = file(comp.layer(i));
    for (k = 0; f && k < R.length; k++)
        for (j = 0; j < R[k].same.length; j++)
            if (!seen[k] && f == R[k].same[j].toLowerCase()) {
                seen[k] = true;
                if (R[k].jsx) run.push(R[k].jsx); else missing.push(R[k].name);
            }
}
if (!run.length && !missing.length) run.push(R[sel].jsx);
for (i = 0; i < run.length; i++) $.evalFile(new File(run[i]));
if (missing.length) alert(/*MSG*/ + "\n\n" + missing.join("\n"));
})();
"""


# Скрипт для AE (ExtendScript, ES3). D — данные из export(); всё — одним шагом отмены.
JSX = r"""(function () {
var D = /*DATA*/;
app.beginUndoGroup("Smooth Cursor");
try {
    var comp = app.project.activeItem, fresh = !(comp instanceof CompItem), folder = null, footage = null, cursor = null;
    function imp(path) {
        if (!folder) folder = app.project.items.addFolder(D.name);
        var it = app.project.importFile(new ImportOptions(new File(path)));
        it.parentFolder = folder;
        return it;
    }
    function source() {  // исходник записи — один на все её фрагменты; уже в проекте (прошлое нажатие) — тот же
        for (var i = 1; !footage && i <= app.project.numItems; i++)
            if (app.project.item(i) instanceof FootageItem && among(path(app.project.item(i)), [D.video]))
                footage = app.project.item(i);
        return footage || (footage = imp(D.video));
    }
    function vec(prop, a, pad) {  // у 2D-слоя Position, Anchor и Scale — из трёх чисел: третье как есть (pad) или 0
        var v = prop.value;
        for (var i = a.length; i < v.length; i++) a.push(pad ? v[i] : 0);
        return a;
    }
    function ease(prop, speed) {  // скорость по всем измерениям значения (у Position — одна, по пути)
        var n = prop.isSpatial || !(prop.value instanceof Array) ? 1 : prop.value.length, a = [];
        for (var i = 0; i < n; i++) a.push(new KeyframeEase(i < 2 ? speed : 0, 100 / 3));
        return a;
    }
    // ключи: Position — [кадр, x, y, скорость, касательная до, после]; число — [кадр, значение, скорость до, после];
    // at(кадр записи) — время в композиции, k — во сколько раз фрагмент медленнее записи (скорости — делим)
    function keys(prop, K, value, at, k) {
        var times = [], vals = [], i;
        for (i = 0; i < K.length; i++) {
            var v = value(K[i]);
            times.push(at(K[i][0]));
            vals.push(v instanceof Array ? vec(prop, v, true) : v);
        }
        if (K.length == 1) { prop.setValue(vals[0]); return; }
        prop.setValuesAtTimes(times, vals);
        for (i = 0; i < K.length; i++) {
            var n = i + 1, sp = prop.isSpatial, din = K[i][sp ? 3 : 2] / k, dout = K[i][3] / k;
            prop.setInterpolationTypeAtKey(n, KeyframeInterpolationType.BEZIER, KeyframeInterpolationType.BEZIER);
            prop.setTemporalAutoBezierAtKey(n, false);  // скорости — наши, AE их не пересчитывает
            prop.setTemporalContinuousAtKey(n, din == dout);
            prop.setTemporalEaseAtKey(n, ease(prop, din), ease(prop, dout));
            if (sp) {
                prop.setSpatialAutoBezierAtKey(n, false);
                prop.setSpatialContinuousAtKey(n, true);
                prop.setSpatialTangentsAtKey(n, vec(prop, K[i][4]), vec(prop, K[i][5]));
            }
        }
    }
    function hold(prop, K, value, at) {  // ключи с удержанием: [кадр, значение]
        if (K.length == 1) return prop.setValue(value(K[0][1]));
        var ts = [], vs = [];
        for (var i = 0; i < K.length; i++) { ts.push(at(K[i][0])); vs.push(value(K[i][1])); }
        prop.setValuesAtTimes(ts, vs);
        for (i = 1; i <= prop.numKeys; i++)
            prop.setInterpolationTypeAtKey(i, KeyframeInterpolationType.HOLD, KeyframeInterpolationType.HOLD);
    }
    function cursorItem() {
        // один вид курсора — сама картинка; несколько — прекомпозиция: кадр i — вид i, hotspot у всех в точке box
        if (cursor) return cursor;
        if (D.sprites.length == 1) return cursor = imp(D.sprites[0].png);
        var m = D.sprites[0];
        cursor = app.project.items.addComp(D.name + " · cursor", D.box[2], D.box[3], 1, D.sprites.length / D.fps, D.fps);
        for (var i = 0; i < D.sprites.length; i++) {
            var s = D.sprites[i], C = cursor.layers.add(imp(s.png)), ct = C.transform, q = 100 * s.scale / m.scale;
            C.name = s.type;
            C.startTime = i / D.fps;
            C.outPoint = (i + 1) / D.fps;
            ct.anchorPoint.setValue(vec(ct.anchorPoint, [s.hx, s.hy], true));
            ct.position.setValue(vec(ct.position, [D.box[0], D.box[1]], true));
            ct.scale.setValue(vec(ct.scale, [q, q], true));
        }
        cursor.parentFolder = folder;
        return cursor;
    }
    // Курсор на фрагмент видео: один слой, ключи движения — прямо на нём. Время — по слою видео: где кадр 0
    // записи, во сколько раз фрагмент ускорен или замедлен; видим там же, где видео (обрезанное — тоже)
    function addCursor(video) {
        var t0 = video.startTime, k = video.stretch / 100, multi = D.sprites.length > 1, m = D.sprites[0];
        function at(n) { return t0 + n / D.fps * k; }
        var L = comp.layers.add(cursorItem()), lt = L.transform;
        L.name = "Smooth Cursor";
        if (multi) L.timeRemapEnabled = true;  // прекомпозиция короче видео: с Time Remap слой тянется сколько нужно
        L.startTime = video.inPoint;  // сначала время слоя: сдвиг начала сдвигает и ключи
        L.inPoint = video.inPoint;
        L.outPoint = video.outPoint;
        if (multi) {  // какой вид курсора — кадр прекомпозиции (середина кадра — без промаха)
            var tr = L.property("ADBE Time Remapping"), ts = [], vs = [], j, q;
            for (j = 0; j < D.kind.length; j++) { ts.push(at(D.kind[j][0])); vs.push((D.kind[j][1] + 0.5) / D.fps); }
            tr.setValuesAtTimes(ts, vs);
            for (j = tr.numKeys; j >= 1; j--) {  // ключи AE от включения — убрать после своих: без ключей Remap выключится
                for (q = 0; q < ts.length && Math.abs(tr.keyTime(j) - ts[q]) > 1e-4; q++) {}
                if (q == ts.length) tr.removeKey(j);
            }
            for (j = 1; j <= tr.numKeys; j++)
                tr.setInterpolationTypeAtKey(j, KeyframeInterpolationType.HOLD, KeyframeInterpolationType.HOLD);
        }
        L.moveBefore(video);
        L.parent = video;  // координаты — в пикселях видео: двигаете или масштабируете видео — курсор с ним
        lt.anchorPoint.setValue(vec(lt.anchorPoint, multi ? [D.box[0], D.box[1]] : [m.hx, m.hy], true));
        if (D.pos.length) keys(lt.position, D.pos, function (q) { return [q[1], q[2]]; }, at, k);
        keys(lt.rotation, D.rot, function (q) { return q[1]; }, at, k);
        keys(lt.scale, D.scl, function (q) { return [q[1], q[1]]; }, at, k);
        hold(lt.opacity, D.show, function (v) { return v; }, at);
        L.motionBlur = D.blur;
    }

    if (fresh) {  // нет открытой композиции — новая по записи, размытие как в программе
        comp = app.project.items.addComp(D.name, D.w, D.h, 1, D.frames / D.fps, D.fps);
        comp.shutterAngle = D.angle;
        comp.shutterPhase = D.phase;
        comp.openInViewer();
    }
    // фрагменты этой записи, что уже в композиции (Dynamic Link из Premiere: Replace with After Effects Composition):
    // каждый становится двумя слоями — исходник (вместо готового со вшитым курсором) и курсор; обрезка, время,
    // скорость и положение — как были. Нет таких — запись целиком от текущего времени
    function path(item) {
        try { return item.file.fsName.toLowerCase(); } catch (e) { return ""; }
    }
    function file(L) {
        return path(L.source);
    }
    function hasCursor(video) {  // уже с курсором (нажали ещё раз) — второй не кладём
        for (var i = 1; i <= comp.numLayers; i++)
            if (comp.layer(i).name == "Smooth Cursor" && comp.layer(i).parent && comp.layer(i).parent.index == video.index)
                return true;
        return false;
    }
    function among(f, list) {
        for (var i = 0; i < list.length; i++) if (f && f == list[i].toLowerCase()) return true;
        return false;
    }
    var videos = [], i;
    for (i = 1; !fresh && i <= comp.numLayers; i++) if (among(file(comp.layer(i)), D.same)) videos.push(comp.layer(i));
    if (!videos.length) {
        var v = comp.layers.add(source());
        v.startTime = fresh ? 0 : comp.time;
        videos.push(v);
    }
    var done = 0;
    for (i = 0; i < videos.length; i++) {
        if (hasCursor(videos[i])) continue;
        if (among(file(videos[i]), D.baked)) videos[i].replaceSource(source(), false);
        addCursor(videos[i]);
        done++;
    }
    if (!done) alert(D.has);
    if (fresh) comp.parentFolder = folder;
    if (D.blur) comp.motionBlur = true;  // переключатель «Enable Motion Blur» у композиции
} catch (e) {
    alert("Smooth Cursor: " + e.toString() + (e.line ? " (line " + e.line + ")" : ""));
} finally {
    app.endUndoGroup();
}
})();
"""
