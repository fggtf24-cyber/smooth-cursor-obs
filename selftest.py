"""Самопроверка без OBS: python selftest.py"""
import copy
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np

import render
import settings
import smoothing
import win32cursor as wc

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
    # точка клика (hotspot) остаётся на месте при повороте и сжатии
    a = np.zeros((40, 40, 4), np.uint8)
    a[8:13, 8:13] = a[30:33, 20:23] = 255  # метка на hotspot (10,10) и вторая — чтобы было что поворачивать
    from PIL import Image
    for deg, scale in ((15, 0.82), (90, 1.0), (-40, 0.6)):
        im, (hx, hy) = render.press_sprite(Image.fromarray(a, "RGBA"), (10, 10), scale, deg)
        assert np.array(im)[round(hy), round(hx), 3] > 128, (deg, scale, "hotspot уехал")
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
        subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=5120x2160:r=30:d=2", "-f", "lavfi",
                        "-i", "sine=d=2", "-c:v", "hevc_nvenc", "-c:a", "aac", str(video)], check=True)
        log = synth_log()
        log["display"].update(width=5120, height=2160)
        (tmp / "src.cursor.json").write_text(json.dumps(log))
        for codec in ("hevc_nvenc", "av1_nvenc", "libx264"):
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
            if codec == "hevc_nvenc":  # превью с 1.0 с: кадры совпадают с полным рендером, включая уход в 1.5 с
                prev = render.render(video, tmp / "src.cursor.json", cfg, progress=lambda pct: False, clip=(1.0, 0.7))
                for k in range(12, 19):
                    assert np.abs(crop(prev, k) - crop(out, 30 + k)).max() < 20, ("превью сдвинуто во времени", k)
                prev.unlink()
                print("превью: ок")
        assert (tmp / "src_cursor.csv").read_text(encoding="utf-8").count("\n") == src["frames"] + 1


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
                         export_keyframes=False, cursor_scale=1.0)
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

            def luma(accurate, clip=None):  # яркость Y как есть (16 — чёрный)
                c = copy.deepcopy(cfg)
                c["render"]["blur_accurate"] = accurate
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
    finally:
        render.encoder_args = args
    print("точный motion blur: ок")


def check_presets():
    import copy
    cfg = copy.deepcopy(settings.DEFAULTS)
    assert settings.preset_of(cfg) == "standard", "настройки по умолчанию = пресет «Стандарт»"
    for name in settings.PRESETS:
        settings.apply_preset(cfg, name)
        assert settings.preset_of(cfg) == name, name
    cfg["smoothing"]["stiffness"] += 10
    assert settings.preset_of(cfg) is None, "после ручной правки пресет не подсвечен"
    print("пресеты: ок")


if __name__ == "__main__":
    check_presets()
    check_smoothing()
    check_click_animation()
    check_obs_transform()
    check_render()
    check_accurate_blur()
    print("selftest OK")
