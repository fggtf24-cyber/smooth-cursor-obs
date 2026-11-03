"""Запись скринкаста через OBS с плавным курсором.

    python smooth_cursor.py                     # ждать хоткей, писать, рендерить
    python smooth_cursor.py --rerender FILE     # перерендерить с текущими настройками (исходник, _smooth или лог)
    python smooth_cursor.py --rerender FILE --debug
"""
import argparse
import base64
import ctypes as C
import ctypes.wintypes as W
import io
import json
import logging
import math
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np
import obsws_python as obs
from PIL import Image

import win32cursor as wc  # первым: включает DPI awareness
from i18n import t

log = logging.getLogger("smooth")
CURSOR_KEY = {"monitor_capture": "capture_cursor", "window_capture": "cursor", "game_capture": "capture_cursor"}
VIDEO_EXT = (".mov", ".mp4", ".mkv", ".flv", ".ts", ".m4v")


def rerender_paths(args):
    """Исходное видео и лог по любому файлу записи: исходнику, готовому _smooth(_debug) или логу .cursor.json."""
    p = Path(args[0])
    stem = p.name[:-len(".cursor.json")] if p.name.endswith(".cursor.json") else p.stem
    orig = re.sub(r"_smooth(_debug)?$", "", stem)  # поверх готового _smooth курсор нарисовался бы дважды
    if orig == stem and p.suffix.lower() in VIDEO_EXT:
        video = p
    else:
        video = next((v for v in (p.with_name(orig + e) for e in VIDEO_EXT) if v.exists()), None)
    logs = [Path(a) for a in args[1:]] + [p.with_name(orig + ".cursor.json")]
    log_path = next((x for x in logs if x.exists()), None)
    if not video or not video.exists():
        raise FileNotFoundError(t("не нашёл исходное видео «{}» ({}) в {}").format(orig, ", ".join(VIDEO_EXT), p.parent))
    if not log_path:
        raise FileNotFoundError(t("не нашёл лог курсора {}").format(logs[-1]))
    return video, log_path


def _align(ox, oy, align, cx, cy):
    """add_alignment() из OBS с теми же целочисленными округлениями. align: 1 лево, 2 право, 4 верх, 8 низ."""
    cx, cy = int(cx), int(cy)
    ox += cx if align & 2 else 0 if align & 1 else int(cx / 2)
    oy += cy if align & 8 else 0 if align & 4 else int(cy / 2)
    return ox, oy


def item_matrix(tf):
    """Матрица 3×3 «пиксель источника → координаты сцены» по трансформации элемента сцены OBS.
    Повторяет update_item_transform() / calculate_bounds_data() из libobs: кроп, масштаб или рамка
    (растянуть / вписать / заполнить / по ширине / по высоте / только уменьшать) с выравниванием,
    точка привязки, поворот, позиция."""
    cl, ct = tf["cropLeft"], tf["cropTop"]
    cx, cy = tf["sourceWidth"] - cl - tf["cropRight"], tf["sourceHeight"] - ct - tf["cropBottom"]
    sx, sy, bt = tf["scaleX"], tf["scaleY"], tf["boundsType"]
    ox = oy = 0
    if bt != "OBS_BOUNDS_NONE" and cx > 0 and cy > 0:
        bw, bh = tf["boundsWidth"], tf["boundsHeight"]
        w, h = cx * abs(sx), cy * abs(sy)
        if bt == "OBS_BOUNDS_MAX_ONLY" and (w > bw or h > bh):
            bt = "OBS_BOUNDS_SCALE_INNER"
        if bt in ("OBS_BOUNDS_SCALE_INNER", "OBS_BOUNDS_SCALE_OUTER"):
            k = bw / w if (bw / bh < w / h) != (bt == "OBS_BOUNDS_SCALE_OUTER") else bh / h
            sx, sy = sx * k, sy * k
        elif bt == "OBS_BOUNDS_SCALE_TO_WIDTH":
            sx, sy = sx * bw / w, sy * bw / w
        elif bt == "OBS_BOUNDS_SCALE_TO_HEIGHT":
            sx, sy = sx * bh / h, sy * bh / h
        elif bt == "OBS_BOUNDS_STRETCH":
            sx, sy = math.copysign(bw / cx, sx), math.copysign(bh / cy, sy)
        ox, oy = _align(ox, oy, tf["boundsAlignment"], -(bw - cx * sx), -(bh - cy * sy))
        box_w, box_h = bw, bh
    else:
        box_w, box_h = cx * sx, cy * sy
    ox, oy = _align(ox, oy, tf["alignment"], box_w, box_h)
    r = math.radians(tf["rotation"])
    S = np.array([[sx, 0, -sx * cl - ox], [0, sy, -sy * ct - oy], [0, 0, 1.0]])  # кроп, масштаб, точка привязки
    R = np.array([[math.cos(r), -math.sin(r), tf["positionX"]], [math.sin(r), math.cos(r), tf["positionY"]], [0, 0, 1]])
    return R @ S


class Obs:
    def __init__(self, c):
        kw = dict(host=c["host"], port=c["port"], password=c["password"], timeout=5)
        self.req = obs.ReqClient(**kw)
        self.ev = obs.EventClient(**kw)
        self.started, self.stopped = threading.Event(), threading.Event()
        self.t_started, self.out_path = 0, None
        self.pauses, self.files = [], []  # паузы [начало, конец]; файлы записи [(с какого момента, путь)]
        self.cursor_ok = set()  # захваты, где курсор в кадр не попадает (проверено при этих экране и настройках)

        def on_record_state_changed(d):
            stamp = time.perf_counter_ns()  # штамп первым делом: от него отсчитывается t0 лога
            s = d.output_state
            if s == "OBS_WEBSOCKET_OUTPUT_STARTED":
                self.t_started = stamp
                self.files = [(stamp, getattr(d, "output_path", None))]
                self.started.set()
            elif s == "OBS_WEBSOCKET_OUTPUT_PAUSED":
                self.pauses.append([stamp, None])
            elif s == "OBS_WEBSOCKET_OUTPUT_RESUMED" and self.pauses:
                self.pauses[-1][1] = stamp
            elif s == "OBS_WEBSOCKET_OUTPUT_STOPPED":
                self.out_path = d.output_path
                self.stopped.set()

        def on_record_file_changed(d):  # OBS разбивает запись на файлы (websocket 5.5+): дальше пишет в новый
            self.files.append((time.perf_counter_ns(), d.new_output_path))

        self.ev.callback.register([on_record_state_changed, on_record_file_changed])

    def prepare(self, c):
        v = self.req.get_version()
        log.info(t("Подключился к OBS %s (websocket %s)"), v.obs_version, v.obs_web_socket_version)

    def pick_display(self, c, select):
        """Смотрит текущую сцену и настройки OBS: выключает захват курсора, выбирает дисплей и считает, где он
        на холсте (позиция, масштаб, рамка, кроп, поворот источника). Вызывается перед каждой записью."""
        scene = self.req.get_current_program_scene().scene_name
        monitors, settings = {}, {}  # monitor_id → (матрица, трансформация) первого включённого захвата
        for name, kind, on, m, tr in self._capture_inputs(scene):
            if name not in settings:
                s = settings[name] = self.req.get_input_settings(name).input_settings
                key = CURSOR_KEY[kind]
                if s.get(key, self.req.get_input_default_settings(kind).default_input_settings.get(key, True)):
                    if c["disable_capture_cursor"]:
                        self.req.set_input_settings(name, {key: False}, True)
                        log.info(t("Выключил захват курсора в источнике «%s»"), name)
                    else:
                        log.warning(t("В источнике «%s» включён захват курсора — на видео будет два курсора"), name)
            if kind == "monitor_capture" and on:
                monitors.setdefault(settings[name].get("monitor_id"), (m, tr, name))
        d = wc.find_display(select, list(monitors))
        vs = self.req.get_video_settings()
        canvas = [vs.base_width, vs.base_height]
        if d["id"] in monitors:
            m, tr, d["source"] = monitors[d["id"]]
            d["method"] = settings[d["source"]].get("method", 0)
            crop = [tr["cropLeft"], tr["cropTop"], tr["sourceWidth"] - tr["cropRight"], tr["sourceHeight"] - tr["cropBottom"]]
        else:
            m = np.diag([canvas[0] / d["width"], canvas[1] / d["height"], 1.0])
            crop = [0, 0, d["width"], d["height"]]
            log.warning(t("В сцене «%s» нет «Захвата экрана» дисплея %s — считаю, что он растянут на весь холст"),
                        scene, d["device"])
        d.update(scene=scene, canvas=canvas, fps=vs.fps_numerator / vs.fps_denominator,
                 profile=self.req.get_profile_list().current_profile_name,
                 map={"m": m[:2].ravel().round(6).tolist(), "canvas": canvas, "crop": crop})
        d["zoom"] = abs(np.linalg.det(m[:2, :2])) ** 0.5
        log.info(t("Профиль «%s», сцена «%s»: дисплей %s %dx%d → холст %dx%d @ %.3g fps, захват ×%.2f в (%d, %d)"),
                 d["profile"], scene, d["device"], d["width"], d["height"], *canvas, d["fps"], d["zoom"],
                 m[0, 2], m[1, 2])
        return d

    def _capture_inputs(self, scene, group=False, enabled=True, m=np.eye(3)):
        """(имя, тип, включён, матрица «пиксель источника → холст», трансформация) для захватов сцены и вложенных."""
        items = (self.req.get_group_scene_item_list if group else self.req.get_scene_item_list)(scene).scene_items
        for it in items:
            on = enabled and it["sceneItemEnabled"]
            mi = m @ item_matrix(it["sceneItemTransform"])
            if it.get("isGroup"):
                yield from self._capture_inputs(it["sourceName"], True, on, mi)
            elif it.get("sourceType") == "OBS_SOURCE_TYPE_SCENE":
                yield from self._capture_inputs(it["sourceName"], False, on, mi)
            elif it.get("inputKind") in CURSOR_KEY:
                yield it["sourceName"], it["inputKind"], on, mi, it["sceneItemTransform"]

    SCENE, SOURCE = "Smooth Cursor", "Smooth Cursor · Display"

    def setup_scene(self, d, fps):
        """Настройка OBS под экран d: холст и выход = разрешение экрана, fps; сцена «Smooth Cursor» с захватом
        этого экрана без курсора на весь холст; сцена становится текущей. Метод захвата — «Автоматически» (DXGI): у него
        задержка кадра ровная (±3 мс), у «Windows 10 (1903 и новее)» — гуляет на ±12 мс, а при 30 fps и от записи к
        записи на десятки мс. Но если Windows рисует курсор программно (крупный указатель — 4K с масштабом 200%,
        увеличенный размер в «Специальных возможностях»), при DXGI он попадает в кадр, хотя захват курсора выключен, —
        тогда метод «Windows 10»: он отдаёт картинку без курсора."""
        w, h = d["width"], d["height"]
        self.req.set_video_settings(int(fps), 1, w, h, w, h)
        if self.SCENE not in [s["sceneName"] for s in self.req.get_scene_list().scenes]:
            self.req.create_scene(self.SCENE)
        props = {"monitor_id": d["id"], "capture_cursor": False, "method": 0}  # 0 — авто, 1 — DXGI, 2 — Windows 10
        if self.SOURCE in [i["inputName"] for i in self.req.get_input_list().inputs]:
            self.req.set_input_settings(self.SOURCE, props, True)
        else:
            self.req.create_input(self.SCENE, self.SOURCE, "monitor_capture", props, True)
        try:
            item = self.req.get_scene_item_id(self.SCENE, self.SOURCE).scene_item_id
        except Exception:
            item = self.req.create_scene_item(self.SCENE, self.SOURCE, True).scene_item_id
        self.req.set_scene_item_transform(self.SCENE, item, {
            "positionX": 0.0, "positionY": 0.0, "rotation": 0.0, "alignment": 5, "boundsType": "OBS_BOUNDS_STRETCH",
            "boundsWidth": float(w), "boundsHeight": float(h), "boundsAlignment": 0,
            "cropLeft": 0, "cropTop": 0, "cropRight": 0, "cropBottom": 0})
        self.req.set_current_program_scene(self.SCENE)
        self.check_cursor(dict(d, source=self.SOURCE, method=0))
        log.info(t("OBS настроен: сцена «%s», %s %dx%d @ %d fps"), self.SCENE, d["device"], w, h, fps)

    def check_cursor(self, d):
        """Перед записью и при настройке: не попадает ли курсор в кадр захвата экрана d["source"], хотя захват курсора
        выключен. Если попадает — метод захвата «Windows 10 (1903 и новее)», он отдаёт картинку без курсора. Проверка
        двигает курсор, поэтому делается раз на захват при этих экране, масштабе и размере курсора."""
        key = (d.get("source"), d["id"], d.get("method"), d["width"], d["height"], d["dpi"], wc.cursor_base_size())
        if not d.get("source") or d.get("method") == 2 or key in self.cursor_ok:
            return
        if not self.cursor_baked(d):
            self.cursor_ok.add(key)
        elif sys.getwindowsversion().build >= 18362:  # «Windows 10» — с Windows 10 1903
            self.req.set_input_settings(d["source"], {"method": 2}, True)
            log.warning(t("Windows рисует курсор прямо в картинку экрана (крупный указатель или масштаб экрана) — "
                          "захват экрана переключён на метод «Windows 10 (1903 и новее)», иначе курсор попал бы в видео"))
        else:
            log.warning(t("Курсор попадает в кадр «%s», хотя захват курсора выключен — на видео будет два курсора"),
                        d["source"])

    def cursor_baked(self, d):
        """Попадает ли курсор в кадр источника d["source"], хотя захват курсора выключен: на снимках OBS с курсором в
        двух точках экрана под ним должна быть сама картинка курсора, пиксель в пиксель (а не то, что на экране меняется
        само — видео, анимации, подсветка при наведении). Курсор на миг сдвигается и возвращается на место."""
        def shot(x, y):
            for k in range(10):  # программный перенос может оставить курсор скрытым (скрыт при наборе текста) —
                wc.user32.SetCursorPos(x + k % 2, y)  # шевелим на пиксель, пока Windows его не покажет
                time.sleep(0.05)
                ci.cbSize = C.sizeof(wc.CURSORINFO)
                if wc.user32.GetCursorInfo(C.byref(ci)) and ci.flags & 1 and not k % 2:
                    break
            time.sleep(0.35)  # кадр OBS с новым положением курсора
            sprite = None
            for _ in range(10):  # программа под курсором могла как раз сменить его — снимок старого не выйдет
                ci.cbSize = C.sizeof(wc.CURSORINFO)
                if wc.user32.GetCursorInfo(C.byref(ci)) and ci.flags & 1 and (sprite := wc.handle_sprite(ci.hCursor)):
                    break
                time.sleep(0.05)
            b = self.req.get_source_screenshot(d["source"], "png", d["width"], d["height"], -1).image_data
            img = np.asarray(Image.open(io.BytesIO(base64.b64decode(b.split(",", 1)[1]))).convert("L"), int)
            return img, sprite

        def match(x, y):  # доля непрозрачных пикселей курсора, совпавших с кадром
            img, sprite = shot(d["left"] + x, d["top"] + y)
            if not sprite or img.shape != (d["height"], d["width"]):
                return 0
            a = np.asarray(sprite[0].convert("LA"), int)
            m = a[..., 1] > 230
            if not m.any() or np.ptp(a[..., 0][m]) < 100:  # без контраста (сплошной курсор) совпасть может и с фоном
                return 0
            best = 0
            for dx in (-1, 0, 1):  # hotspot у тонких курсоров (рука) может выйти на пиксель в сторону
                for dy in (-1, 0, 1):
                    x0, y0 = x - round(sprite[1][0]) + dx, y - round(sprite[1][1]) + dy
                    box = img[y0:y0 + m.shape[0], x0:x0 + m.shape[1]]
                    if box.shape == m.shape:
                        best = max(best, (np.abs(box - a[..., 0])[m] < 40).mean())
            return best

        pt, ci = W.POINT(), wc.CURSORINFO(cbSize=C.sizeof(wc.CURSORINFO))
        wc.user32.GetCursorPos(C.byref(pt))
        time.sleep(1)  # источник мог только что создаться или перенастроиться — OBS ещё отдаёт старые кадры
        try:
            r = [match(d["width"] // 2, d["height"] // 2), match(d["width"] * 3 // 4, d["height"] // 2)]
        finally:
            wc.user32.SetCursorPos(pt.x, pt.y)
        log.debug("cursor check: match %.2f %.2f", *r)
        return min(r) > 0.85

    def start(self):
        if self.req.get_record_status().output_active:
            raise RuntimeError(t("в OBS уже идёт запись — остановите её"))
        self.started.clear()
        self.stopped.clear()
        self.out_path, self.pauses, self.files = None, [], []
        self.req.start_record()
        if not self.started.wait(10):
            raise RuntimeError(t("OBS не подтвердил старт записи за 10 с"))
        return self.t_started

    def elapsed(self):
        """(секунд на видео, на паузе ли сейчас) — для таймера в окне."""
        now = time.perf_counter_ns()
        paused = sum((b or now) - a for a, b in self.pauses)
        return (now - self.t_started - paused) / 1e9, bool(self.pauses) and self.pauses[-1][1] is None

    def pause_toggle(self):
        """Пауза записи в OBS; уже на паузе — продолжить."""
        (self.req.resume_record if self.elapsed()[1] else self.req.pause_record)()

    def stop(self):
        resp = self.req.stop_record()
        self.stopped.wait(60)  # STOPPED приходит, когда файл дописан
        return Path(self.out_path or resp.output_path)


def parse_hotkey(s):
    *mods, key = s.lower().replace(" ", "").split("+")
    flags = 0x4000  # MOD_NOREPEAT
    for m in mods:
        flags |= {"alt": 1, "ctrl": 2, "shift": 4, "win": 8}[m]
    if key[0] == "f" and key[1:].isdigit():
        return flags, 0x6F + int(key[1:])  # VK_F1 = 0x70
    if len(key) == 1 and key.isalnum():
        return flags, ord(key.upper())
    raise ValueError(t("непонятный хоткей: {}").format(s))


class HotkeyThread(threading.Thread):
    """Глобальный хоткей: RegisterHotKey в своём потоке со своим циклом сообщений; callback — из этого потока."""

    def __init__(self, hotkey, callback):
        super().__init__(daemon=True)
        self.hotkey, self.mods, self.vk = hotkey, *parse_hotkey(hotkey)
        self.callback, self.tid, self.error = callback, None, None
        self._ready = threading.Event()

    def start(self):
        super().start()
        self._ready.wait()
        if self.error:
            raise RuntimeError(self.error)
        return self

    def run(self):
        self.tid = C.windll.kernel32.GetCurrentThreadId()
        if not wc.user32.RegisterHotKey(None, 1, self.mods, self.vk):
            self.error = t("хоткей {} уже занят другой программой").format(self.hotkey)
            self._ready.set()
            return
        self._ready.set()
        msg = W.MSG()
        while wc.user32.GetMessageW(C.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                self.callback()
        wc.user32.UnregisterHotKey(None, 1)

    def stop(self):
        if self.tid and self.is_alive():
            wc.user32.PostThreadMessageW(self.tid, 0x0012, 0, 0)  # WM_QUIT
            self.join(2)


class Recorder:
    """OBS + логгер курсора. cfg — живой словарь настроек: изменения действуют со следующей записи."""

    def __init__(self, cfg):
        c = cfg["obs"]
        try:
            self.obs = Obs(c)
        except Exception as e:
            raise ConnectionError(t("не удалось подключиться к OBS ws://{}:{} ({}). OBS запущен? Websocket включён "
                                    "(Сервис → Настройки сервера WebSocket)? Порт и пароль верны?")
                                  .format(c["host"], c["port"], e))
        self.obs.prepare(c)
        self.cfg, self.rec = cfg, None

    @property
    def recording(self):
        return self.rec is not None

    def pick_display(self):
        return self.obs.pick_display(self.cfg["obs"], self.cfg["display"])

    def start(self):
        display = self.pick_display()
        if self.cfg["obs"]["disable_capture_cursor"] and self.cfg["obs"]["check_cursor"]:
            self.obs.check_cursor(display)
        logger = wc.CursorLogger(display, self.cfg["logger"]["hz"])
        logger.start()  # лог пишется с запасом до старта; t0 задаёт событие OBS
        try:
            t0 = self.obs.start()
        except Exception:
            logger.stop()
            raise
        self.rec = (logger, t0)
        log.info(t("● Запись идёт"))

    def stop(self):
        """Останавливает запись и кладёт лог курсора рядом с каждым её файлом (OBS может разбивать запись).
        Возвращает [(видео или None, лог)]."""
        (logger, t0), self.rec = self.rec, None
        try:
            video = self.obs.stop()
        except Exception as e:
            log.error(t("Не удалось остановить запись в OBS: %s"), e)
            video = None
        finally:
            logger.stop()
        if video:
            files = self.obs.files or [(t0, None)]
            files[-1] = (files[-1][0], video)  # последний файл точно называет событие остановки
        else:  # лог не теряем: в папку записей (текущая папка у установленной программы — её собственная)
            folder = Path(self.cfg["ui"]["folder"] or Path.home() / "Videos")
            folder.mkdir(parents=True, exist_ok=True)
            files = [(t0, folder / f"cursor_{time.strftime('%Y%m%d_%H%M%S')}.cursor.json")]
        out = []
        for i, (begin, path) in enumerate(files):
            if not path:
                log.warning(t("OBS не сообщил имя файла записи — лог для него не сохранён"))
                continue
            path = Path(path)
            log_path = path if path.name.endswith(".cursor.json") else path.with_suffix(".cursor.json")
            end = files[i + 1][0] if i + 1 < len(files) else None
            log_path.write_text(json.dumps(logger.to_dict(begin, self.obs.pauses, end), separators=(",", ":")),
                                encoding="utf-8")
            out.append((path if video else None, log_path))
            log.info(t("■ Запись остановлена: %s"), path if video else None)
        return out

    def close(self):
        for client in (self.obs.ev, self.obs.req):
            try:
                client.disconnect()
            except Exception:
                pass


def record_loop(cfg):
    """Консольный режим: хоткей → запись → рендер в фоне."""
    import queue
    import render
    r = Recorder(cfg)
    try:
        r.pick_display()  # показать заранее; перед записью дисплей выбирается заново
    except LookupError as e:
        log.warning("%s\nНастройте сцену OBS до нажатия %s.", e, cfg["hotkey"])
    presses = queue.Queue()
    hk = HotkeyThread(cfg["hotkey"], lambda: presses.put(1)).start()
    log.info("Готово. %s — старт/стоп записи, Ctrl+C — выход.", cfg["hotkey"])

    def render_bg(video, log_path):
        try:
            render.render(video, log_path, cfg)
        except Exception as e:
            log.error("Рендер не удался: %s\n  Перерендер: python smooth_cursor.py --rerender \"%s\"", e, video)

    try:
        while True:
            try:
                presses.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if not r.recording:
                    r.start()
                else:
                    parts = [p for p in r.stop() if p[0]]
                    threading.Thread(target=lambda: [render_bg(*p) for p in parts]).start()
            except Exception as e:
                log.error("Ошибка: %s", e)
    except KeyboardInterrupt:
        if r.recording:
            r.stop()
    finally:
        hk.stop()
        r.close()


def main():
    import settings
    ap = argparse.ArgumentParser(description="Скринкаст через OBS с плавным курсором (окно программы — app.pyw)")
    ap.add_argument("--config", default=settings.PATH)
    ap.add_argument("--rerender", nargs="+", metavar="FILE",
                    help="перерендерить запись: исходник, готовый _smooth.mp4 или .cursor.json (лог можно вторым)")
    ap.add_argument("--debug", action="store_true", help="рисовать поверх сырой курсор (проверка синхронизации)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("obsws_python").setLevel(logging.CRITICAL)
    cfg = settings.load(a.config)
    if a.debug:
        cfg["render"]["debug_raw"] = True
    try:
        if a.rerender:
            import render
            render.render(*rerender_paths(a.rerender), cfg)
        else:
            record_loop(cfg)
    except (LookupError, RuntimeError, OSError, ValueError) as e:
        raise SystemExit(f"Ошибка: {e}")


if __name__ == "__main__":
    main()
