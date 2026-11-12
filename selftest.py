"""Самопроверка без OBS: python selftest.py"""
import copy
import json
import struct
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

import cursorcore
import render
import settings
import smoothing
import platform_cursor as wc

SM = settings.DEFAULTS["smoothing"]


def synth_log():
    """Рывок (100,100)→(1000,400) за 0.5 с, тремор ±2 px, клик в 800 мс, уход с дисплея после 1500 мс."""
    rng = np.random.default_rng(1)
    t = np.arange(-200, 2000, 1000 / 240)
    u = np.clip(t / 500, 0, 1)
    e = u * u * (3 - 2 * u)
    x = np.round(100 + 900 * e + rng.integers(-2, 3, len(t)) * (t > 500))
    y = np.round(100 + 300 * e + rng.integers(-2, 3, len(t)) * (t > 500))
    x[t > 1500] = -50
    samples = [[a, b, c, 1 if 600 < a < 700 else 0, 1] for a, b, c in zip(t.tolist(), x.tolist(), y.tolist())]
    at = lambda ms: (float(np.interp(ms, t, x)), float(np.interp(ms, t, y)))
    clicks = [[800.0, "L", 1, *at(800)], [880.0, "L", 0, *at(880)]]
    return {"display": {"width": 1920, "height": 1080, "dpi": 96}, "types": wc.TYPES, "samples": samples,
            "clicks": clicks}


def check_smoothing():
    log = synth_log()
    for method in ("spring", "one_euro"):
        tr = smoothing.frame_track(log, 60, 120, {**SM, "method": method}, 0)
        f = lambda ms: int(round(ms * 60 / 1000))
        n = f(800)
        assert abs(tr["x"][n] - tr["raw_x"][n]) < 0.5 and abs(tr["y"][n] - tr["raw_y"][n]) < 0.5, (method, "клик уплыл")
        still = slice(f(1050), f(1450))
        assert np.std(tr["raw_x"][still]) > 1 and np.std(tr["x"][still]) < 0.3, (method, "дрожание не погашено")
        assert tr["x"][: f(500)].max() < 1000 + 3, (method, "перелёт")
        assert tr["visible"][f(1400)] and not tr["visible"][f(1600)], (method, "видимость")
        assert tr["type"][f(650)] == 1 and tr["type"][f(400)] == 0, (method, "тип курсора")
        assert abs(tr["click_age"][f(833)] - 33.3) < 1 and tr["click_age"][f(400)] < 0, (method, "время клика")
        tg, sx = tr["path"][:2]  # с шагом 1 мс: когда сглаженный курсор проходит точку, где рука была в 250 мс
        raw = np.asarray(log["samples"], float)
        lag = tg[np.argmax(sx >= np.interp(250, raw[:, 0], raw[:, 1]))] - 250
        assert abs(lag) <= 10, (method, "курсор отстаёт от руки — подсветка на экране убежит вперёд", lag)
        # перетаскивание 1 px/мс, отпущено на ходу: после отпускания курсор не откатывается назад
        t = np.arange(0, 1500, 1000 / 240)
        drag = {"display": {"width": 4000, "height": 1000}, "samples": [[a, 100 + min(max(a - 200, 0), 500), 500, 0, 1]
                                                                       for a in t.tolist()],
                "clicks": [[200.0, "L", 1], [700.0, "L", 0]]}
        for name in settings.PRESETS:
            cfg = {"smoothing": {**SM, "method": method}, "render": {}}
            settings.apply_preset(cfg, name)
            x = smoothing.frame_track(drag, 1000, 1500, {**cfg["smoothing"], "method": method}, 0)["x"]
            assert x[700:].min() > 600 - 0.5, (method, name, "откат после перетаскивания", 600 - x[700:].min())
        print(f"{method}: ок; на середине рывка расхождение с рукой {lag:+.0f} мс")


def check_click_animation():
    c = render.click_curve(np.array([-5, 0, 40, 80, 200, 320, 1e9, np.inf]), 320)
    assert c[0] == 0 and c[1] == 0 and 0.5 < c[2] < 1 and c[3] == 1 and 0 < c[4] < 1 and c[5] == 0 and c[6] == c[7] == 0
    held = render.click_curve(np.array([80, 1500, 2080, 2400]), 320, 2000)  # держали кнопку 2 с
    assert held[0] == 1 and held[1] == 1 and 0 < held[2] < 1 and held[3] == 0, ("удержание", held)
    assert render.click_curve(np.array(5000.0), 320, np.inf) == 1, "пока кнопка зажата — наклон держится"
    v = np.array([2.0, 0.05, 0, -2.0])  # ширин кадра в секунду, плюс — вправо
    deg = render.tilt_angle(v, 5, 5)  # вправо — на 5° вправо, влево — на 5° влево
    assert abs(deg[0] + 5) < 0.1 and -1 < deg[1] < 0 and deg[2] == 0 and abs(deg[3] - 5) < 0.1, \
        ("вправо — наклон вправо (по часовой), медленно — чуть-чуть", deg)
    ramp = -render.tilt_angle(np.array([0.1, 0.3, 0.6, 1.2]), 5, 5)
    assert (np.diff(ramp) > 0.5).all() and 3 < ramp[2] < 4.5, ("наклон растёт со скоростью", ramp)
    assert not render.tilt_angle(v, 0, 0).any(), "0° — наклон выключен, курсор как в системе"
    lr = render.tilt_angle(v, 10, 30)
    assert abs(lr[0] + 10) < 0.1 and abs(lr[3] - 30) < 0.1, ("вправо и влево — свои углы", lr)
    step = render.smooth_tilt(np.r_[np.zeros(60), np.full(60, -27.0)], 60)  # рывок с места: поворот плавный
    assert np.abs(np.diff(step)).max() < 4 and abs(step[59] + step[60] + 27) < 1, ("сглаживание угла", step)
    assert len(render.smooth_tilt(np.ones(3), 60)) == 3, "короткое видео"
    # клик на полном ходу (72° вправо): наклон сходит на нет к нажатию и возвращается — поворот без рывков
    age = (np.arange(120) - 60) * 1000 / 60
    lvl = np.round(render.click_curve(age, 320) * (render.CLICK_STEPS - 1)).astype(int)
    tl = render.frame_tilt(np.full(120, 2.0), np.ones(120, bool), lvl, 60, 72, 27)
    rot = 15 * lvl / (render.CLICK_STEPS - 1) + tl * render.TILT_STEP_DEG
    assert not tl[lvl > 0].any() and tl[20] < -65 and np.abs(np.diff(rot)).max() < 12, \
        ("переход от наклона к клику", np.abs(np.diff(rot)).max())
    # стрелка: в покое как в Windows (остриё скошено на 22.5°), на ходу поворачивается на заданный угол в обе стороны
    big = wc.cursor_sprite("arrow", 512)  # мерить углы — на крупном: у 32 px на кончике пара пикселей

    def axis(vel, right=72, left=27):  # куда смотрит остриё: биссектриса левого и правого края у кончика, ° (плюс — низ вправо)
        k = round(float(render.tilt_angle(vel, right, left)) / render.TILT_STEP_DEG)
        a = np.array(render.pose_sprite(*big, settings.DEFAULTS["render"], 0, k)[0].getchannel("A")) > 128
        ys, xs = np.nonzero(a)
        rows = np.arange(ys.min() + 3, ys.min() + (ys.max() - ys.min()) // 4)
        sl = [np.polyfit(rows, [f(xs[ys == y]) for y in rows], 1)[0] for f in (np.min, np.max)]
        return np.degrees(np.arctan(sl).mean())
    rest, right, left = axis(0.0), axis(2.0), axis(-2.0)  # «С наклоном» 72/27: вправо — зеркально тому, как влево
    assert abs(rest - 22.5) < 2 and abs(right + 49.5) < 2 and abs(left - 49.5) < 2, ("стрелка на ходу", rest, right, left)
    right, left = axis(2.0, -5, -5), axis(-2.0, -5, -5)  # минус — назад от позы Windows: вправо — остриё на 5° влево
    assert abs(right - 27.5) < 2 and abs(left - 17.5) < 2, ("стрелка, наклон назад", right, left)
    # точка клика (hotspot) остаётся на месте при повороте и сжатии
    a = np.zeros((40, 40, 4), np.uint8)
    a[8:13, 8:13] = a[30:33, 20:23] = 255  # метка на hotspot (10,10) и вторая — чтобы было что поворачивать
    from PIL import Image
    for deg, scale in ((15, 0.82), (90, 1.0), (-40, 0.6)):
        im, (hx, hy) = render.press_sprite(Image.fromarray(a, "RGBA"), (10, 10), scale, deg)
        assert np.array(im)[round(hy), round(hx), 3] > 128, (deg, scale, "hotspot уехал")
    # пустой (невидимый) курсор программы — прозрачная точка, а не падение
    assert render.press_sprite(Image.new("RGBA", (40, 40)), (10, 10), 0.5, 0)[0].size == (1, 1), "пустой курсор"
    print("анимация клика: ок")


def check_obs_transform():
    """Пиксель дисплея → холст OBS по трансформации источника (как считает сам OBS)."""
    from smooth_cursor import item_matrix
    base = {"alignment": 5, "boundsAlignment": 0, "boundsType": "OBS_BOUNDS_NONE", "boundsWidth": 0, "boundsHeight": 0,
            "cropLeft": 0, "cropTop": 0, "cropRight": 0, "cropBottom": 0, "positionX": 0, "positionY": 0,
            "rotation": 0, "scaleX": 1, "scaleY": 1, "sourceWidth": 1920, "sourceHeight": 1080}
    cases = [  # (изменения трансформации, точка источника, ожидаемая точка на холсте)
        ({"boundsType": "OBS_BOUNDS_STRETCH", "boundsWidth": 1920, "boundsHeight": 1080}, (700, 300), (700, 300)),
        # «Подогнать к экрану»: 1080p в холст 5120×2160 — масштаб 2, по центру по горизонтали
        ({"boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsWidth": 5120, "boundsHeight": 2160}, (0, 0), (640, 0)),
        ({"boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsWidth": 5120, "boundsHeight": 2160}, (1920, 1080), (4480, 2160)),
        ({"scaleX": 0.5, "scaleY": 0.5, "alignment": 0, "positionX": 960, "positionY": 540}, (0, 0), (480, 270)),
        ({"cropLeft": 100, "cropTop": 40}, (100, 40), (0, 0)),
        ({"rotation": 90, "positionX": 500}, (10, 0), (500, 10)),  # OBS крутит по часовой
    ]
    for change, src, want in cases:
        m = item_matrix({**base, **change})
        got = m @ [*src, 1]
        assert np.allclose(got[:2], want, atol=0.51), (change, src, want, got[:2])
    print("трансформация OBS: ок")


def check_render():
    """Синтетическое 5K-видео с аудио → рендер обоими кодеками → проверка ffprobe и пикселей."""
    cfg = settings.load()
    cfg["sync"]["offset_ms"] = 0
    cfg["render"].update(click_animation=True, motion_blur=True, debug_raw=False, export_keyframes=True)
    ff = render.tool(cfg["render"]["ffmpeg"], "ffmpeg")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        video = tmp / "src.mp4"
        codecs = [c for c in ("hevc_nvenc", "av1_nvenc") if render.encoder_works(ff, c)] + ["libx264"]  # в CI видеокарты нет
        subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=5120x2160:r=30:d=2", "-f", "lavfi",
                        "-i", "sine=d=2", "-c:v", codecs[0], "-c:a", "aac", str(video)], check=True)
        log = synth_log()
        log["display"].update(width=5120, height=2160)
        (tmp / "src.cursor.json").write_text(json.dumps(log))
        for codec in codecs:
            cfg["render"]["codec"] = codec
            out = render.render(video, tmp / "src.cursor.json", cfg)
            src, dst = render.probe(render.tool(cfg["render"]["ffmpeg"], "ffprobe"), video), \
                render.probe(render.tool(cfg["render"]["ffmpeg"], "ffprobe"), out)
            assert (dst["w"], dst["h"], dst["fps"], dst["frames"]) == (src["w"], src["h"], src["fps"], src["frames"])
            assert abs(dst["duration"] - src["duration"]) < 1 / 30, (src["duration"], dst["duration"])

            def crop(path, n):  # 64×64 вокруг точки, где курсор стоит после рывка (1000, 400)
                raw = subprocess.run([ff, "-v", "error", "-i", str(path), "-vf",
                                      f"select=eq(n\\,{n}),crop=64:64:990:390,format=gray", "-frames:v", "1",
                                      "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
                return np.frombuffer(raw, np.uint8).astype(int)

            stand, gone = crop(out, 39), crop(out, 54)  # 1.3 с — курсор стоит; 1.8 с — ушёл с дисплея
            assert np.ptp(gone) < 10, "после ухода с дисплея курсор должен скрыться"
            assert np.abs(stand - np.median(gone)).max() > 60, "курсора нет там, где он должен стоять"
            print(f"render {codec}: ок, {dst['w']}x{dst['h']} @ {dst['fps']:g} fps, {dst['frames']} кадров")
            if codec == codecs[0]:  # превью с 1.0 с: кадры совпадают с полным рендером, включая уход в 1.5 с
                prev = render.render(video, tmp / "src.cursor.json", cfg, progress=lambda pct: False, clip=(1.0, 0.7))
                for k in range(12, 19):
                    # сдвиг на кадр даёт 125 там, где курсор уезжает; без видеокарты шум x264 в ffmpeg 9 — до 25
                    assert np.abs(crop(prev, k) - crop(out, 30 + k)).max() < (20 if "nvenc" in codec else 60), (
                        "превью сдвинуто во времени", k, [int(np.abs(crop(prev, k) - crop(out, 30 + k + d)).max())
                                                          for d in (-2, -1, 0, 1, 2)])
                prev.unlink()
                print("превью: ок")
        assert (tmp / "src_cursor.csv").read_text(encoding="utf-8").count("\n") == src["frames"] + 1


def check_cursor_elsewhere():
    """Всю запись курсор на другом мониторе: видео всё равно получается, просто без курсора."""
    cfg = settings.load()
    cfg["sync"]["offset_ms"] = 0
    ff = render.tool(cfg["render"]["ffmpeg"], "ffmpeg")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        video = tmp / "src.mp4"
        subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=640x360:r=30:d=1", "-c:v", "libx264",
                        str(video)], check=True)
        log = synth_log()
        log["display"].update(width=640, height=360)
        log["samples"] = [[s[0], 300, 1500, s[3], s[4]] for s in log["samples"]]
        log["clicks"] = []
        (tmp / "src.cursor.json").write_text(json.dumps(log))
        for accurate, blur in ((True, True), (False, True), (False, False)):
            cfg["render"].update(blur_accurate=accurate, motion_blur=blur, debug_raw=False, codec="libx264")
            out = render.render(video, tmp / "src.cursor.json", cfg, progress=lambda pct: False)
            assert render.probe(render.tool(cfg["render"]["ffmpeg"], "ffprobe"), out)["frames"] == 30, (accurate, blur)
    print("курсор на другом мониторе: ок")


def check_accurate_blur():
    """Точный motion blur. В покое (клик, смена курсора) — пиксель в пиксель как обычный режим: значит, слой и его
    позиция попадают в свой кадр. В движении — смаз сплошной и несёт столько же света, сколько неподвижный курсор.
    Превью с дробного начала — те же кадры, что полный рендер."""
    W, H, FPS = 1920, 1080, 30
    t = np.arange(-200, 4000, 1000 / 240)
    x = np.clip(300 + 3.0 * (t - 1600), 300, 1800)  # стоит → рывок вправо 3000 px/с (1600–2100 мс) → стоит
    log = {"display": {"width": W, "height": H, "dpi": 96}, "types": wc.TYPES,
           "samples": [[a, float(b), 300.0, int(1000 <= a < 1300), 1] for a, b in zip(t.tolist(), x)],
           "clicks": [[300.0, "L", 1, 300, 300], [380.0, "L", 0, 300, 300],
                      [2600.0, "L", 1, 1800, 300], [2700.0, "L", 0, 1800, 300]]}
    cfg = settings.load()
    cfg["sync"]["offset_ms"] = 0
    cfg["smoothing"] = copy.deepcopy(SM)  # не зависеть от выбранного пресета
    cfg["render"].update(codec="libx264", click_animation=True, motion_blur=True, shutter=30, debug_raw=False,
                         export_keyframes=False, cursor_scale=1.0, morph_animation=False)  # смена курсора — за кадр
    ff = render.tool(cfg["render"]["ffmpeg"], "ffmpeg")
    args, render.encoder_args = render.encoder_args, lambda codec, cq, preset: ["-qp", "0", "-pix_fmt", "yuv420p"]
    try:  # без потерь (crf 0 — нет): сравниваем пиксели
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            video, lp = tmp / "b.mp4", tmp / "b.cursor.json"
            subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}:d=4", "-c:v",
                            "libx264", "-qp", "0", "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries",
                            "bt709", "-color_trc", "bt709", "-color_range", "tv", str(video)], check=True)
            lp.write_text(json.dumps(log))

            def luma(accurate, clip=None, tilt=0):  # яркость Y как есть (16 — чёрный)
                c = copy.deepcopy(cfg)
                c["render"].update(blur_accurate=accurate, motion_tilt_right_deg=tilt, motion_tilt_left_deg=tilt)
                out = render.render(video, lp, c, progress=lambda pct: False, clip=clip)
                raw = subprocess.run([ff, "-v", "error", "-i", str(out), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
                                     capture_output=True, check=True).stdout
                out.unlink()
                return np.frombuffer(raw, np.uint8).reshape(-1, W * H * 3 // 2)[:, :W * H].reshape(-1, H, W) \
                    .astype(int) - 16

            G, A = luma(False), luma(True)
            assert len(A) == len(G) == 4 * FPS
            # курсор стоит — за всю выдержку кадра сглаженный путь не сдвинулся (сглаживание без задержки начинает
            # движение чуть раньше руки, поэтому берём по траектории, а не по времени рывка)
            tr = smoothing.frame_track(log, FPS, 4 * FPS, cfg["smoothing"], 0)
            tg, sx = tr["path"][:2]
            still = [n for n in range(4 * FPS) if np.ptp(np.interp(
                np.linspace(tr["t"][n] - 1000 / 30 - 2, tr["t"][n], 40), tg, sx)) < 0.01]
            assert {10, 15, 30, 85} <= set(still), still  # клик, смена курсора, второй клик
            bad = [n for n in still if (A[n] != G[n]).any()]
            assert not bad and (G[10] != G[2]).any(), ("в покое точный ≠ обычному", bad)
            for n in (56, 58, 60):
                row = np.argmax((A[n] > 0).sum(1))  # самая длинная строка следа (фон чёрный, без потерь)
                on = np.nonzero(A[n][row] > 0)[0]
                assert (np.diff(on) == 1).all() and on[-1] - on[0] > 80, (n, "смаз с разрывами или короткий")
                assert abs(A[n].sum() / A[40].sum() - 1) < 0.03, (n, "смаз несёт не столько света, сколько курсор")
            P = luma(True, clip=(1.73, 0.5))
            assert len(P) == 15 and all((P[k] == A[52 + k]).all() for k in range(len(P))), "превью сдвинуто"
            # наклон в движении: на ходу курсор (и шлейф) наклонён, в покое — тот же, что без наклона
            for ref, acc in ((G, False), (A, True)):
                T = luma(acc, tilt=5)
                # угол сглажен без задержки, как траектория: наклон начинается чуть раньше рывка и тает чуть позже
                calm = [n for n in still if np.ptp(np.interp(np.linspace(tr["t"][n] - 250, tr["t"][n] + 250, 60),
                                                             tg, sx)) < 0.5]
                assert {10, 15, 30, 85} <= set(calm), calm
                bad = [n for n in calm if (T[n] != ref[n]).any()]
                assert not bad, ("наклон в покое", acc, bad)
                assert all((T[n] != ref[n]).any() for n in (56, 58, 60)), ("на ходу нет наклона", acc)
    finally:
        render.encoder_args = args
    print("точный motion blur и наклон в движении: ок")


def check_ae():
    """Ключи для After Effects: их мало, и кривая AE по ним (Bezier, влияние 1/3) идёт по траектории в допуске."""
    import ae
    cfg = copy.deepcopy(settings.DEFAULTS)
    rc, fps, n = cfg["render"], 60.0, 120
    tr = render.cursor_track(synth_log(), {"w": 1920, "h": 1080, "fps": fps, "frames": n}, cfg)[0]
    pos = ae.position_keys(tr["vx"], tr["vy"], tr["visible"], {48}, fps)
    assert len(pos) < n / 4 and 48 in [k[0] for k in pos], ("ключи позиции", [k[0] for k in pos])
    sp = np.hypot(np.gradient(tr["vx"]), np.gradient(tr["vy"]))
    for (a, xa, ya, _, _, out), (b, xb, yb, _, inn, _) in zip(pos, pos[1:]):
        T = (b - a) / fps  # скорости обратно из касательных — проверяем ровно то, что уйдёт в AE
        c = ae.path_curve(np.array([xa, ya]), np.array([xb, yb]), np.array(out) * 3 / T, -np.array(inn) * 3 / T,
                          np.arange(b - a + 1) / (b - a), T)
        err = np.hypot(c[:, 0] - tr["vx"][a:b + 1], c[:, 1] - tr["vy"][a:b + 1])
        assert (err <= ae.TOL_PX + ae.TOL_MOVE * sp[a:b + 1] + 1e-3).all(), ("позиция мимо", a, b, err.max())
    scale = 100 * (1 - (1 - rc["click_scale"]) * tr["press"])
    keys = ae.value_keys(scale, fps, ae.TOL_PCT, [47])
    assert 3 <= len(keys) <= 10 and 47 in [k[0] for k in keys], ("ключи масштаба", [k[0] for k in keys])
    for (a, va, _, out), (b, vb, inn, _) in zip(keys, keys[1:]):
        c = ae.hermite(va, vb, out, inn, np.arange(b - a + 1) / (b - a), (b - a) / fps)
        assert np.abs(c - scale[a:b + 1]).max() <= ae.TOL_PCT + 1e-3, ("масштаб мимо", a, b)
    assert ae.value_keys(np.full(n, 100.0), fps, ae.TOL_PCT) == [(0, 100.0, 0.0, 0.0)], "без клика — один ключ"
    # лишних нет: без любого промежуточного ключа кривая выходит из допуска (иначе его можно было бы удалить)
    p, v = np.stack([tr["vx"], tr["vy"]], 1), np.stack([np.gradient(tr["vx"]), np.gradient(tr["vy"])], 1) * fps
    tol = ae.TOL_PX + ae.TOL_MOVE * np.hypot(*v.T) / fps
    tol[48] = min(tol[48], ae.TOL_CLICK)
    ks = [k[0] for k in pos]
    for a, m, b in zip(ks, ks[1:], ks[2:]):
        c = ae.path_curve(p[a], p[b], v[a], v[b], np.arange(b - a + 1) / (b - a), (b - a) / fps)
        assert c is None or (np.hypot(*(c - p[a:b + 1]).T) / tol[a:b + 1]).max() > 1, ("лишний ключ позиции", m)
    for (a, va, _, oa), (m, *_), (b, vb, ib, _) in zip(keys, keys[1:], keys[2:]):
        c = ae.hermite(va, vb, oa, ib, np.arange(b - a + 1) / (b - a), (b - a) / fps)
        assert np.abs(c - scale[a:b + 1]).max() > ae.TOL_PCT, ("лишний ключ масштаба", m)
    print(f"after effects: ок, {len(pos)} ключей позиции и {len(keys)} масштаба на {n} кадров")


def check_presets():
    import copy
    cfg = copy.deepcopy(settings.DEFAULTS)
    assert settings.preset_of(cfg) == "standard", "настройки по умолчанию = пресет «Стандарт»"
    for name in settings.PRESETS:
        settings.apply_preset(cfg, name)
        assert settings.preset_of(cfg) == name, name
    cfg["smoothing"]["stiffness"] += 10
    assert settings.preset_of(cfg) is None, "после ручной правки пресет не подсвечен"
    settings.save_my_preset(cfg, "Обзор")
    assert settings.preset_of(cfg) == "my:Обзор", "свой пресет узнаётся по имени"
    settings.apply_preset(cfg, "standard")
    settings.apply_preset(cfg, "my:Обзор")
    assert cfg["smoothing"]["stiffness"] == settings.DEFAULTS["smoothing"]["stiffness"] + 10, "свой пресет применяется"
    cfg["smoothing"]["stiffness"] += 10
    settings.save_my_preset(cfg, "Туториал")
    assert [p["name"] for p in settings.my_presets(cfg)] == ["Обзор", "Туториал"], "новое имя — добавляется"
    settings.save_my_preset(cfg, "Обзор")
    mine = settings.my_presets(cfg)
    assert [p["name"] for p in mine] == ["Обзор", "Туториал"] and mine[0]["stiffness"] == cfg["smoothing"]["stiffness"], \
        "то же имя — обновляется на месте"
    settings.delete_my_preset(cfg, "Обзор")
    assert [p["name"] for p in settings.my_presets(cfg)] == ["Туториал"], "удаление"
    cfg["my_presets"] = ["мусор", {"name": ""}, {"name": 5}, None]
    assert settings.my_presets(cfg) == [], "кривые записи в конфиге пропускаются"
    for junk in (5, "abc", {"name": "x"}, None):
        cfg["my_presets"] = junk
        assert settings.my_presets(cfg) == [] and settings.preset_of(cfg) is None, ("кривой my_presets", junk)
    print("пресеты: ок")


def check_custom_cursor():
    """Свой курсор программы (не из схемы Windows): снимок в лог, одинаковые хэндлы — один тип, рендер в масштабе."""
    lg = wc.CursorLogger({"left": 0, "top": 0, "dpi": 96})
    h = wc.user32.LoadImageW(None, r"C:\Windows\Cursors\aero_pen.cur", 2, 0, 0, 0x50)  # LR_LOADFROMFILE | DEFAULTSIZE
    seen = {}
    assert lg._capture(h, seen) == lg._capture(h, seen) == len(wc.TYPES) and lg._capture(None, seen) == 0
    custom = json.loads(json.dumps(lg.to_dict(0)))["custom"]
    img, _ = wc.cursor_sprite("custom0", round(lg.ref * 2), custom)
    assert img.width == round(lg.custom["custom0"]["ref"] * 2) and img.getchannel("A").getextrema()[1] == 255
    img2, _ = wc.cursor_sprite("custom0", round(lg.ref * 2), custom, app_scale=1.5)  # «Курсоры программ» × 1.5
    assert abs(img2.width - img.width * 1.5) <= 1, (img.width, img2.width)
    print("свой курсор программы: ок")


def check_cursor_theme():
    """Вид курсора для рендера: схема Windows, свой .cur и виды из файлов рядом по именам паков."""
    black = wc.theme_files("scheme:Windows Black")
    assert black["arrow"].lower().endswith("arrow_r.cur") and black["ibeam"].lower().endswith("beam_r.cur"), black
    a, _ = wc.cursor_sprite("arrow", 64)
    b, _ = wc.cursor_sprite("arrow", 64, theme="scheme:Windows Black")
    assert a.tobytes() != b.tobytes() and b.getchannel("A").getextrema()[1] == 255
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(r"C:\Windows\Cursors")
        for name, f in (("Normal.cur", "aero_arrow.cur"), ("Link.cur", "aero_link.cur"), ("Text.cur", "beam_r.cur"),
                        ("Busy.ani", "aero_busy.ani"), ("Working.ani", "aero_working.ani"), ("Diagonal2.cur", "aero_nesw.cur"),
                        ("Handwriting.cur", "aero_pen.cur"), ("Vertical.cur", "aero_ns.cur")):
            shutil.copy(src / f, Path(tmp) / name)
        files = {k: Path(v).name for k, v in cursorcore.pack_files(Path(tmp) / "Normal.cur").items()}
        assert files == {"arrow": "Normal.cur", "hand": "Link.cur", "ibeam": "Text.cur", "wait": "Busy.ani",
                         "busy": "Working.ani", "size_nesw": "Diagonal2.cur", "size_ns": "Vertical.cur"}, files
        img, hot = cursorcore.cur_sprite(Path(tmp) / "Normal.cur", 64)  # путь Linux: без Windows, через Pillow
        assert img.width == 64 and hot[0] < 8 and hot[1] < 8 and img.getchannel("A").getextrema()[1] == 255, (img.size, hot)
    assert cursorcore.pack_files("нет такого.cur") == {}
    print("вид курсора: ок")


def check_morph():
    """Анимация смены вида курсора: шаги по кадрам, спрайт перехода, рендер обоими шлейфами и слои для AE."""
    # «Не менять на миг»: рука на 3 кадра (100 мс при 30 fps) пропадает, на 20 — остаётся; в конце видео — как есть
    k, v = np.array([0] * 10 + [1] * 3 + [0] * 10 + [1] * 20 + [2] * 2), np.ones(45, bool)
    assert render.hold_types(k, v, 150, 30).tolist() == [0] * 23 + [1] * 20 + [2] * 2
    assert render.hold_types(k, v, 0, 30).tolist() == k.tolist()
    import ae
    kind, vis = np.array([0, 0, 1, 1, 1, 1, 1, 1, 1, 0, 0, 2, 2]), np.ones(13, bool)
    vis[9:11] = False  # курсор спрятан, потом появился другим видом — без анимации
    step, prev = render.morph_steps(kind, vis, 30, 160)
    assert step[:2].tolist() == [0, 0] and (np.diff(step[2:7]) > 0).all() and step[2] >= 1 and step[7:].sum() == 0, step
    assert (prev[2:7] == 0).all(), prev
    for amount in (0, 0.12, 0.3):  # отскок: проскакивает ровно на заданное, в конце — ровно 1 и без рывка
        g = [render.bounce_curve(u, amount) for u in np.linspace(0, 1, 401)]
        assert abs(max(g) - 1 - amount) < 0.002 and g[0] == 0 and abs(g[-1] - 1) < 1e-9 and abs(g[-1] - g[-2]) < 1e-3,             ("отскок", amount, max(g))
    # с наклоном в движении: смена вида его не трогает — на ходу курсор так и остаётся наклонённым
    cfg = copy.deepcopy(settings.DEFAULTS)
    cfg["render"].update(morph_animation=True, motion_tilt_right_deg=72, motion_tilt_left_deg=27)
    t = np.arange(-200, 3000, 1000 / 240)
    x = np.clip(100 + 1.0 * (t - 200), 100, 3000)  # едет вправо, в 1000 мс стрелка → текстовый курсор
    lg = {"display": {"width": 3840, "height": 1080, "dpi": 96}, "types": wc.TYPES, "clicks": [],
          "samples": [[a, float(b), 300.0, int(a >= 1000), 1] for a, b in zip(t.tolist(), x)]}
    info = {"w": 3840, "h": 1080, "fps": 30, "frames": 80}
    tr = render.cursor_track(lg, info, cfg)[0]
    m = tr["morph"] > 0
    plain = render.cursor_track(lg, info, {**cfg, "render": {**cfg["render"], "morph_animation": False}})[0]
    assert m.any() and tr["tilt"][m].all() and (tr["tilt_deg"] == plain["tilt_deg"]).all(), \
        ("смена вида сбивает наклон", tr["tilt"][m].tolist())
    old, new = wc.cursor_sprite("arrow", 128), wc.cursor_sprite("hand", 128)
    # в конце смены наклонённый курсор — ровно обычный наклонённый: картинка и точка клика на тех же местах
    for deg in (-30, 40):
        a, (ax, ay) = render.morph_sprite(*old, *new, 1, 0, deg=deg)
        b, (bx, by) = render.pose_sprite(*new, cfg["render"], 0, deg / render.TILT_STEP_DEG)
        canvas = [Image.new("L", (160, 160)) for _ in "ab"]
        canvas[0].paste(a.getchannel("A"), (round(80 - ax), round(80 - ay)))
        canvas[1].paste(b.getchannel("A"), (round(80 - bx), round(80 - by)))
        ma, mb = (np.array(c) > 128 for c in canvas)
        # точка вставки округлена до пикселя, а тонкому курсору из темы Linux сдвиг на пиксель заметен
        iou = max((np.roll(ma, (dy, dx), (0, 1)) & mb).sum() / (np.roll(ma, (dy, dx), (0, 1)) | mb).sum()
                  for dy in (-1, 0, 1) for dx in (-1, 0, 1))
        assert iou > (0.9 if sys.platform == "win32" else 0.85), ("наклон смены вида: не совпал с обычным", deg, round(float(iou), 3))
    for p, like in ((0.05, old), (0.95, new)):
        img, (hx, hy) = render.morph_sprite(*old, *new, p)
        a = np.array(img.getchannel("A")) > 128
        ref = np.array(like[0].resize((32, 32), Image.LANCZOS).getchannel("A")) > 128
        assert 0.6 < a.sum() / ref.sum() < 1.6, ("переход: размер", p, a.sum(), ref.sum())
        assert -img.width < hx < 2 * img.width and -img.height < hy < 2 * img.height, ("переход: hotspot", p)
    cfg = settings.load()
    cfg["sync"]["offset_ms"] = 0
    cfg["render"].update(codec="libx264", morph_animation=True, morph_ms=200, morph_min_ms=0, click_animation=True,
                         motion_blur=True, debug_raw=False, export_keyframes=False, motion_tilt_right_deg=72,
                         motion_tilt_left_deg=27)
    ff = render.tool(cfg["render"]["ffmpeg"], "ffmpeg")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        video = tmp / "src.mp4"
        subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=1920x1080:r=30:d=1.5", "-c:v",
                        "libx264", str(video)], check=True)
        log = synth_log()  # стрелка → текстовый курсор в 600 мс → стрелка в 700 мс
        (tmp / "src.cursor.json").write_text(json.dumps(log))
        info = render.probe(render.tool(cfg["render"]["ffmpeg"], "ffprobe"), video)
        tr = render.cursor_track(log, info, cfg)[0]
        assert (tr["morph"] > 0).sum() >= 4, ("шагов смены вида", tr["morph"].tolist())
        for accurate in (False, True):
            cfg["render"]["blur_accurate"] = accurate
            out = render.render(video, tmp / "src.cursor.json", cfg, progress=lambda pct: False)
            assert out.exists(), out
        jsx = ae.export(video, tmp / "src.cursor.json", cfg)[0]
        names = [f.stem for f in jsx.parent.glob("cursor_m*.png")]
        assert names and "cursor_m" in jsx.read_text(encoding="utf-8-sig"), ("в AE нет шагов смены вида", names)
    print("смена вида курсора: ок")


def check_autostart():
    """Фоновый процесс запускает программу только в момент, когда OBS открылся, и выходит по сигналу."""
    import threading
    import time
    from unittest import mock

    import autostart
    obs_up, runs = [], []
    with mock.patch.object(autostart, "obs_running", lambda: obs_up[0] if obs_up else set()), \
            mock.patch.object(autostart.subprocess, "Popen", lambda c: runs.append(c)), \
            mock.patch.object(autostart, "STOP", "Local\\SmoothCursorSelftest"):
        th = threading.Thread(target=autostart.watch, args=(5,))
        th.start()
        for up in (set(), {1}, {1}, set(), {2}, {2, 3}):  # {2, 3}: старый висит, рядом открыли новый
            obs_up[:] = [up]
            time.sleep(0.05)
        autostart.watch(5)  # второй фоновый процесс сразу выходит, не мешая первому
        assert th.is_alive(), "второй процесс сбил первый"
        autostart.signal(autostart.STOP)
        th.join(2)
        assert not th.is_alive(), "не вышел по сигналу"
    assert len(runs) == 3 and all("--with-obs" in c for c in runs), runs
    print("запуск с OBS: ок")


def check_hotkey_released():
    """Хоткей занят, только пока needed(): без OBS клавиша достаётся другим программам (F9 в After Effects)."""
    import time

    import platform_cursor as wc
    from smooth_cursor import HotkeyThread, parse_hotkey
    key = "ctrl+alt+shift+F11"
    mods, vk = parse_hotkey(key)

    def free():
        ok = wc.user32.RegisterHotKey(None, 7, mods, vk)
        if ok:
            wc.user32.UnregisterHotKey(None, 7)
        return bool(ok)

    need = [False]
    hk = HotkeyThread(key, lambda: None, lambda: need[0]).start()  # занятую клавишу видно и без OBS
    try:
        time.sleep(0.3)
        assert free(), "без OBS клавиша не отпущена"
        need[0] = True
        time.sleep(2.5)
        assert not free(), "OBS открыт, а клавиша не занята"
        hk.needed = lambda: 1 / 0  # ошибка в needed() — поток живёт, клавиша остаётся
        time.sleep(2.5)
        assert hk.is_alive() and not free(), "ошибка в needed() убила хоткей"
        hk.needed = lambda: False
        time.sleep(2.5)
        assert free(), "OBS закрыли, а клавиша не отпущена"
    finally:
        hk.stop()
    assert not hk.is_alive()


def check_obs_buttons():
    """Кнопки записи в самом OBS: программа узнаёт о них; свои старт и стоп за чужие не принимает."""
    from types import SimpleNamespace as NS
    from unittest import mock

    import smooth_cursor as sc
    calls, handlers = [], {}

    class Req:
        def __init__(self, **kw):
            pass

        def get_record_status(self):
            return NS(output_active=False)

        def start_record(self):
            handlers["on_record_state_changed"](NS(output_state="OBS_WEBSOCKET_OUTPUT_STARTED"))

        def stop_record(self):
            calls.append("stop_record")
            fire("STOPPED", output_path="own.mkv")
            return NS(output_path="own.mkv")

    class Ev:
        def __init__(self, **kw):
            self.callback = NS(register=lambda fs: handlers.update({f.__name__: f for f in fs}))

    def fire(state, **kw):
        handlers["on_record_state_changed"](NS(output_state="OBS_WEBSOCKET_OUTPUT_" + state, **kw))

    with mock.patch.object(sc.obs, "ReqClient", Req), mock.patch.object(sc.obs, "EventClient", Ev):
        o = sc.Obs({"host": "", "port": 0, "password": ""})
    seen = []
    o.external = seen.append
    o.start()
    assert o.stop() == Path("own.mkv") and calls == ["stop_record"] and not seen, ("своя запись — чужая", seen)
    calls.clear()
    fire("STARTED", output_path="obs.mkv")
    fire("PAUSED")
    assert seen == ["started"] and o.t_started and o.elapsed()[1], "старт из OBS не замечен"
    fire("STOPPED", output_path="obs.mkv")
    assert seen == ["started", "stopped"] and o.stop() == Path("obs.mkv") and not calls, \
        ("остановленную в OBS запись не останавливаем второй раз", calls)
    calls.clear()

    class NotReady(Req):  # загружающийся OBS: подключился, но на запросы отказ — соединения надо закрыть
        def get_version(self):
            raise RuntimeError("207")

        def disconnect(self):
            calls.append("req closed")

    class Ev2(Ev):
        def disconnect(self):
            calls.append("ev closed")

    with mock.patch.object(sc.obs, "ReqClient", NotReady), mock.patch.object(sc.obs, "EventClient", Ev2):
        try:
            sc.Recorder({"obs": {"host": "", "port": 0, "password": ""}})
            raise AssertionError("отказ OBS проглочен")
        except RuntimeError:
            pass
    assert calls == ["ev closed", "req closed"], ("соединения остались открытыми — OBS не завершится", calls)
    print("кнопки записи в OBS: ок")


def check_render_toast():
    """Плашка рендера: примерное время и угол экрана при разных экранах, масштабах и панели задач."""
    assert render.eta_left(0, 0.5, 5) is None and render.eta_left(10, 60, 0.5) is None, "рано судить"
    assert abs(render.eta_left(10, 60, 50) - 40) < 1e-6 and render.eta_left(10, 100, 50) == 0
    assert [render.eta_text(s) for s in (None, 3, 42, 60, 83, 700)] == [
        "считаю время…", "почти готово", "осталось ≈ 40 с", "осталось ≈ 1 мин", "осталось ≈ 1 мин 20 с",
        "осталось ≈ 12 мин"], [render.eta_text(s) for s in (None, 3, 42, 60, 83, 700)]

    def mon(w, h, dpi, work=None, left=0, top=0):
        return {"left": left, "top": top, "width": w, "height": h, "dpi": dpi,
                "work": work or [left, top, left + w, top + h - 40 * dpi // 96]}

    for m in (mon(1366, 768, 96), mon(1920, 1080, 120), mon(2560, 1440, 96), mon(3840, 2160, 192),
              mon(5120, 1440, 96), mon(1280, 720, 144), mon(1080, 1920, 96),
              mon(1920, 1080, 96, [60, 0, 1920, 1080]), mon(1920, 1080, 96, [0, 40, 1920, 1080]),
              mon(1920, 1080, 96, [0, 0, 1860, 1080])):
        k = m["dpi"] / 96
        w, h = round(340 * k), round(78 * k)
        x, y = wc.corner_pos(m, w, h)
        a, b, c, d = m["work"]
        assert a <= x and b <= y and x + w <= c and y + h <= d, ("плашка вышла за экран", m, x, y)
        assert c - (x + w) == round(16 * k), ("отступ справа не от края", m, x)
        assert d - (y + h) >= round(16 * k), ("отступ снизу", m, y)
    full = mon(1920, 1080, 96, [0, 0, 1920, 1080])
    assert 1080 - (wc.corner_pos(full, 340, 78)[1] + 78) >= 56, "док поверх экрана закрыл бы плашку"
    print("плашка рендера: ок")


def check_virtual_mode():
    """DEVMODEW собран по разметке Windows: поля на своих смещениях."""
    import struct
    from unittest import mock

    import vdisplay

    class DEVMODEW(wc.C.Structure):  # начало структуры — как в wingdi.h
        _fields_ = [("name", wc.C.c_wchar * 32), ("spec", wc.C.c_ushort), ("drv", wc.C.c_ushort),
                    ("size", wc.C.c_ushort), ("extra", wc.C.c_ushort), ("fields", wc.C.c_uint), ("u", wc.C.c_byte * 16),
                    ("color", wc.C.c_short * 5), ("form", wc.C.c_wchar * 32), ("logpix", wc.C.c_ushort),
                    ("bpp", wc.C.c_uint), ("w", wc.C.c_uint), ("h", wc.C.c_uint), ("flags", wc.C.c_uint),
                    ("hz", wc.C.c_uint), ("icm", wc.C.c_uint * 8)]
    got = []

    def change(name, dm, *rest):
        if dm is not None:  # None — применить отложенное
            got.append(DEVMODEW.from_buffer_copy(bytes(dm)))
        return 0

    def enum(name, i, dm):
        if i >= 2:
            return 0
        d = DEVMODEW.from_buffer(dm)
        d.w, d.h, d.hz = (1920, 1080, 60) if i == 0 else (5120, 2160, 144)
        return 1

    with mock.patch.object(wc, "user32", mock.Mock(ChangeDisplaySettingsExW=change, EnumDisplaySettingsW=enum)):
        assert vdisplay.set_mode("x", 2560, 1440, 120) and vdisplay.place("x", 1920, 1080, 3520)
        assert vdisplay.modes("x") == {(1920, 1080, 60), (5120, 2160, 144)}, vdisplay.modes("x")
    assert wc.C.sizeof(DEVMODEW) == 220, wc.C.sizeof(DEVMODEW)
    assert (got[0].size, got[0].w, got[0].h, got[0].hz) == (220, 2560, 1440, 120) and got[0].fields == 0x580000, got[0]
    assert got[1].fields == 0x180020 and struct.unpack("<ii", bytes(got[1].u)[:8]) == (3520, 0), "позиция экрана"
    for text, want in (("5120x2160", "5120x2160"), (" 5120 х 2160 ", "5120x2160"), ("5120×2160", "5120x2160"),
                       ("5120 на 2160", "5120x2160"), ("1367*769", "1366x768")):
        assert vdisplay.parse_size(text) == want, text
    for text in ("", "5120", "abc", "100x100", "9000x2160", "1920x1080x60"):
        try:
            vdisplay.parse_size(text)
        except ValueError:
            continue
        raise AssertionError(("приняли плохое разрешение", text))
    print("режим монитора: ок")


def pod_walk(data):
    """Разбирает POD как libspa: (тип, тело) и проверяет выравнивание. Если формат собран неверно, тут упадёт."""
    size, kind = struct.unpack_from("<II", data)
    body = data[8:8 + size]
    assert len(body) == size and len(data) >= 8 + size + -size % 8, "POD короче заявленного"
    return kind, body, data[8 + size + -size % 8:]


def check_wayland_format():
    import waylandcursor as wl
    assert wl.pod_id(2) == bytes.fromhex("04000000 03000000 02000000 00000000")
    kind, body, rest = pod_walk(wl.format_pod())
    assert (kind, rest) == (wl.T_OBJECT, b"") and struct.unpack_from("<II", body) == (wl.OBJ_FORMAT, wl.PARAM_ENUM_FORMAT)
    props, body = {}, body[8:]
    while body:
        key, flags = struct.unpack_from("<II", body)
        props[key], _, body = (lambda k, b, r: ((k, b), None, r))(*pod_walk(body[8:]))
        assert flags == 0
    assert list(props) == [1, 2, 0x20001, 0x20003, 0x20004], props
    assert props[1] == (wl.T_ID, struct.pack("<I", 2))
    kind, b = props[0x20003]  # размер: выбор из диапазона, значения по 8 байт без заголовков
    assert kind == wl.T_CHOICE and struct.unpack_from("<IIII", b) == (wl.CHOICE_RANGE, 0, 8, wl.T_RECT) and len(b) == 16 + 24
    kind, b = props[0x20001]
    assert struct.unpack_from("<IIII", b) == (wl.CHOICE_ENUM, 0, 4, wl.T_ID) and len(b) == 16 + 20
    for p in wl.stream_params():
        assert pod_walk(p)[0] == wl.T_OBJECT and not pod_walk(p)[2]

    pixels = bytes([10, 20, 30, 255, 0, 0, 0, 0])  # 2×1, BGRA
    head = wl.CURSOR_HEAD.pack(7, 0, 100, -5, 3, 4, wl.CURSOR_HEAD.size)
    blob = head + wl.BITMAP_HEAD.pack(12, 2, 1, 8, wl.BITMAP_HEAD.size) + pixels
    x, y, hx, hy, (img,) = wl.parse_cursor(lambda off, n: blob[off:off + n])
    assert (x, y, hx, hy, img.size) == (100, -5, 3, 4, (2, 1)) and img.getpixel((0, 0)) == (30, 20, 10, 255), img.getpixel((0, 0))
    moved = wl.CURSOR_HEAD.pack(7, 0, 1, 2, 3, 4, 0)
    assert wl.parse_cursor(lambda off, n: moved[off:off + n]) == (1, 2, 3, 4, None), "картинка та же"
    gone = wl.CURSOR_HEAD.pack(0, 0, 0, 0, 0, 0, 0)
    assert wl.parse_cursor(lambda off, n: gone[off:off + n]) is None, "курсор на другом экране"
    empty = wl.CURSOR_HEAD.pack(7, 0, 1, 2, 0, 0, 28) + wl.BITMAP_HEAD.pack(12, 0, 0, 0, 0)
    assert wl.parse_cursor(lambda off, n: empty[off:off + n])[4] == (None,), "скрытый курсор"

    src = wl.WaylandSource.__new__(wl.WaylandSource)  # разбор картинок без настоящего потока
    src.state, src.shapes, src.shape, src.buttons = (0, 0, None, False), {}, None, {0x110}
    src._cursor(wl.parse_cursor(lambda off, n: blob[off:off + n]))
    x, y, shape, showing, left, right = src.read()
    assert (x, y, showing, left, right) == (100, -5, True, True, False) and src.describe(shape)[1][1] == (3, 4)
    src._cursor(None)
    assert src.read()[:4] == (100, -5, shape, False), "курсор ушёл с экрана: точка та же, но не виден"
    print("wayland: формат и метаданные: ок")


def check_wayland_portal():
    """Разговор с порталом на настоящих сообщениях jeepney: подписи разбираются при сборке, ответы — как у GNOME."""
    from unittest import mock

    import jeepney
    import waylandcursor as wl

    class Fd:
        def to_raw_fd(self):
            return 42

    class Conn:
        unique_name = ":1.77"

        def __init__(self, modes=7):
            self.calls, self.modes, self.answers = [], modes, []

        def send_and_get_reply(self, msg, timeout=None):
            msg.serialise(serial=1)  # неверная подпись падает здесь
            f = msg.header.fields
            member = f[jeepney.HeaderFields.member]
            self.calls.append((member, msg.body))
            body = {"Get": (("u", self.modes),), "OpenPipeWireRemote": (Fd(),)}.get(member, ())
            if member in ("CreateSession", "SelectSources", "Start"):
                self.answers.append({"CreateSession": {"session_handle": ("o", "/s/1")}, "SelectSources": {},
                                     "Start": {"restore_token": ("s", "new-token"), "streams": (
                                         "a(ua{sv})", [(55, {"size": ("(ii)", (1920, 1080))})])}}[member])
            return mock.Mock(header=mock.Mock(message_type=jeepney.MessageType.method_return), body=body)

        def filter(self, rule):
            return mock.MagicMock()

        def recv_until_filtered(self, queue, timeout=None):
            return mock.Mock(body=(0, self.answers.pop(0)))

        def send(self, msg):
            self.calls.append(("Close", msg.body))

        def close(self):
            pass

    with tempfile.TemporaryDirectory() as d, mock.patch("jeepney.io.blocking.open_dbus_connection", create=True) as o:
        token = Path(d) / "sub" / "token"
        conn = o.return_value = Conn()
        p = wl.Portal(token)
        assert (p.node, p.size, p.fd) == (55, (1920, 1080), 42) and token.read_text() == "new-token"
        select = next(b for m, b in conn.calls if m == "SelectSources")[1]
        assert select["cursor_mode"] == ("u", 4) and "restore_token" not in select
        p.close()
        assert conn.calls[-1][0] == "Close"
        conn = o.return_value = Conn()
        wl.Portal(token)
        assert next(b for m, b in conn.calls if m == "SelectSources")[1]["restore_token"] == ("s", "new-token"), \
            "экран спросили бы снова"
        o.return_value = Conn(modes=3)  # Sway: положение курсора не отдаёт
        try:
            wl.Portal(token)
        except RuntimeError as e:
            assert "X11" in str(e), e
        else:
            raise AssertionError("без режима Metadata должна быть понятная ошибка")
    print("wayland: портал: ок")


def xcursor_file(sizes):
    """Файл Xcursor: для каждого размера картинка, залитая непрозрачным красным, hotspot = размер / 4."""
    head = b"Xcur" + struct.pack("<III", 16, 0x10000, len(sizes))
    pos, toc, chunks = 16 + 12 * len(sizes), b"", b""
    for s in sizes:
        chunk = struct.pack("<IIIIIIIII", 36, 0xFFFD0002, s, 1, s, s, s // 4, s // 4, 50) + b"\x00\x00\xff\xff" * s * s
        toc += struct.pack("<III", 0xFFFD0002, s, pos)
        chunks += chunk
        pos += len(chunk)
    return head + toc + chunks


def check_old_ffmpeg():
    from unittest import mock
    for banner, want in (("ffmpeg version 4.4.2-0ubuntu0.22.04.1 Copyright", ("-filter_complex_script", "-vsync")),
                         ("ffmpeg version n6.1.1 Copyright", ("-filter_complex_script", "-fps_mode")),
                         ("ffmpeg version 7.1-essentials_build", ("-/filter_complex", "-fps_mode")),
                         ("ffmpeg version N-118000-gabcdef Copyright", ("-/filter_complex", "-fps_mode"))):
        render._major.clear()
        with mock.patch.object(subprocess, "run", return_value=mock.Mock(stdout=banner)), \
                mock.patch.object(render.sys, "platform", "linux"):
            assert (render.graph_option("ff"), render.fps_option("ff")) == want, banner
    render._major.clear()
    print("старый ffmpeg: ок")


def check_linux_cursor():
    from unittest import mock

    import linuxcursor as lc
    img, hot, nominal = lc.read_xcursor(xcursor_file([24, 48]), 30)
    assert (nominal, img.size, hot, img.getpixel((0, 0))) == (48, (48, 48), (12, 12), (255, 0, 0, 255))
    assert lc.read_xcursor(xcursor_file([24, 48]), 96)[2] == 48, "крупнее нет — берём самый крупный"
    half = lc.argb_image([0x80400000], 1, 1).getpixel((0, 0))  # красный с умножением на альфу
    assert half[3] == 128 and abs(half[0] - 128) < 2, half
    assert lc.argb_image([(1 << 40) | 0xFF0000FF], 1, 1).getpixel((0, 0)) == (0, 0, 255, 255), "старшие биты слова X11"

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "Child").mkdir()
        (root / "Child" / "index.theme").write_text("[Icon Theme]\nInherits=Base\n")
        (root / "Base" / "cursors").mkdir(parents=True)
        (root / "Base" / "cursors" / "left_ptr").write_bytes(xcursor_file([32]))
        assert lc.find_cursor(lc.CURSORS["arrow"], "Child", [root]).name == "left_ptr", "наследование темы"
        assert lc.find_cursor(("nothing",), "Child", [root]) is None
        with mock.patch.dict(lc.os.environ, {"XCURSOR_THEME": "Child"}), mock.patch.object(lc, "theme_dirs", lambda: [root]):
            img, hot = lc.cursor_sprite("arrow", 64)
            assert img.size == (64, 64) and hot == (16, 16), (img.size, hot)
    img, hot = lc.fallback_sprite(32)
    assert img.size == (32, 32) and img.getbbox() and hot[0] < 4

    assert lc.parse_hotkey("Ctrl + Shift + F9") == (frozenset({"ctrl", "shift"}), "f9")
    for bad in ("hyper+a", "ctrl+nope"):
        try:
            lc.parse_hotkey(bad)
        except (KeyError, ValueError):
            continue
        raise AssertionError(bad)
    assert (lc.KEYS["f1"], lc.KEYS["f10"], lc.KEYS["a"], lc.KEYS["m"], lc.KEYS["1"]) == (59, 68, 30, 50, 2)
    ev = lc.EVENT.pack(0, 0, 1, 67, 1) + lc.EVENT.pack(0, 0, 2, 0, 5) + lc.EVENT.pack(0, 0, 1, 67, 2) + lc.EVENT.pack(0, 0, 1, 67, 0)
    assert lc.key_events(ev + b"xx") == [(67, 1), (67, 0)], "автоповтор и обрывок чтения не считаем"
    import linuxautostart as la
    assert la.desktop_exec(["/home/u/.local/share/smooth-cursor/Smooth Cursor", "--watch"]) == \
        '"/home/u/.local/share/smooth-cursor/Smooth Cursor" --watch'
    assert la.desktop_exec(["/opt/a$b", "100%"]) == r'"/opt/a\\$b" 100%%', la.desktop_exec(["/opt/a$b", "100%"])
    assert la.desktop_exec(['/x/"q"\\y']) == r'"/x/\\"q\\"\\\\y"', la.desktop_exec(['/x/"q"\\y'])

    mons = [{"id": "0", "width": 1920, "height": 1080, "left": 0, "top": 0, "primary": True},
            {"id": "1", "width": 2560, "height": 1440, "left": 1920, "top": 0}]
    tr = lambda w, h: {"sourceWidth": w, "sourceHeight": h}
    assert lc.capture_id("xshm_input", {"screen": 1}, tr(0, 0)) == "1"
    assert lc.capture_id(lc.PIPEWIRE[0], {}, tr(2560, 1440), mons) == "1"
    assert lc.capture_id(lc.PIPEWIRE[0], {}, tr(3840, 2160), mons) == "0", "масштаб: те же пропорции"
    assert lc.capture_id(lc.PIPEWIRE[0], {}, tr(0, 0), mons) == "0", "поток ещё не начался"
    d = {"width": 1920, "height": 1080, "dpi": 96}
    lc.adjust_display(d, tr(3840, 2160))
    assert d == {"width": 3840, "height": 2160, "dpi": 192}
    lc.adjust_display(d, tr(0, 0))
    assert d["width"] == 3840, "источник ещё без картинки"
    with mock.patch.object(lc, "WAYLAND", True):
        assert lc.capture_input({"id": "0"}, False, ["x", lc.PIPEWIRE[1]]) == (lc.PIPEWIRE[1], {"ShowCursor": False})
    with mock.patch.object(lc, "WAYLAND", False):
        assert lc.capture_input({"id": "1"}, False, []) == ("xshm_input", {"screen": 1, "show_cursor": False})

    class Source:  # логгер Linux без экрана: движение, новый курсор, клик
        steps = [(5, 6, "a", True, False, False), (7, 8, "b", True, True, False), (9, 9, "hid", False, False, False)]

        def read(self):
            return self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]

        def describe(self, shape):
            return {"a": ("hand", None), "b": (None, lc.cursor_sprite("arrow", 8)), "hid": (None, None)}[shape]

    with mock.patch.object(lc, "cursor_base_size", return_value=24):
        lg = lc.CursorLogger({"left": 0, "top": 0, "dpi": 96}, 2000)
    lg.source = Source()
    lg._thread.start()
    while len(lg.samples) < 3:
        lc.time.sleep(0.01)
    lg._stop.set()
    lg._thread.join()
    s = lg.samples
    assert s[0][1:] == (5, 6, lc.TYPES.index("hand"), 1) and s[1][1:] == (7, 8, len(lc.TYPES), 1) and s[2][1:] == (9, 9, 0, 0), s[:3]
    assert [(c[1], c[2], c[3]) for c in lg.clicks] == [("L", 1, 7), ("L", 0, 9)] and "custom0" in lg.custom, lg.clicks
    print("linux: курсоры, хоткей, логгер: ок")


if __name__ == "__main__":
    check_wayland_format()
    check_wayland_portal()
    check_linux_cursor()
    check_old_ffmpeg()
    if sys.platform == "win32":
        check_autostart()
        check_hotkey_released()
        check_virtual_mode()
        check_custom_cursor()
        check_cursor_theme()
    check_obs_buttons()
    check_render_toast()
    check_cursor_elsewhere()
    check_presets()
    check_smoothing()
    check_click_animation()
    check_morph()
    check_obs_transform()
    check_ae()
    check_render()
    check_accurate_blur()
    print("selftest OK")
