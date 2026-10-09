"""Перевод интерфейса и сообщений: ключ — русская строка, значение — английская. t(s) возвращает строку
на текущем языке (LANG = "ru" | "en"); неизвестные строки остаются как есть."""
LANG = "ru"


def t(s):
    return EN.get(s, s) if LANG == "en" else s


EN = {
    # --- запись и OBS ---
    "Подключился к OBS %s (websocket %s)": "Connected to OBS %s (websocket %s)",
    "Выключил захват курсора в источнике «%s»": "Turned off cursor capture in source “%s”",
    "В источнике «%s» включён захват курсора — на видео будет два курсора":
        "Cursor capture is on in source “%s” — the video will show two cursors",
    "В сцене «%s» нет «Захвата экрана» дисплея %s — считаю, что он растянут на весь холст":
        "Scene “%s” has no Display Capture of %s — assuming it fills the whole canvas",
    "Профиль «%s», сцена «%s»: дисплей %s %dx%d → холст %dx%d @ %.3g fps, захват ×%.2f в (%d, %d)":
        "Profile “%s”, scene “%s”: display %s %dx%d → canvas %dx%d @ %.3g fps, capture ×%.2f at (%d, %d)",
    "в OBS уже идёт запись — остановите её": "OBS is already recording — stop it first",
    "OBS не подтвердил старт записи за 10 с": "OBS did not confirm the recording start within 10 s",
    "непонятный хоткей: {}": "unrecognized hotkey: {}",
    "хоткей {} уже занят другой программой": "hotkey {} is already used by another program",
    "не удалось подключиться к OBS ws://{}:{} ({}). OBS запущен? Websocket включён (Сервис → Настройки сервера "
    "WebSocket)? Порт и пароль верны?":
        "could not connect to OBS ws://{}:{} ({}). Is OBS running? Is the WebSocket server enabled "
        "(Tools → WebSocket Server Settings)? Are the port and password right?",
    "● Запись идёт": "● Recording",
    "OBS настроен: сцена «%s», %s %dx%d @ %d fps": "OBS is set up: scene “%s”, %s %dx%d @ %d fps",
    "Не удалось остановить запись в OBS: %s": "Could not stop the recording in OBS: %s",
    "■ Запись остановлена: %s": "■ Recording stopped: %s",
    "не нашёл исходное видео «{}» ({}) в {}": "source video “{}” ({}) not found in {}",
    "не нашёл лог курсора {}": "cursor log not found: {}",
    "Не могу выбрать дисплей: {}. Мониторы:\n{}": "Can't pick a display: {}. Monitors:\n{}",
    "в текущей сцене OBS нет включённого «Захвата экрана» подключённого монитора":
        "the current OBS scene has no enabled Display Capture of a connected monitor",
    "в текущей сцене OBS несколько «Захватов экрана» — оставьте один или задайте display в config.yaml":
        "the current OBS scene has several Display Captures — keep one or pick the display explicitly",
    "монитор {} не найден": "monitor {} not found",
    "мониторов {} несколько — задайте display по имени": "several {} monitors — pick the display by name",
    "монитор «{}» не найден": "monitor “{}” not found",
    "под «{}» подходит несколько мониторов": "several monitors match “{}”",
    # --- рендер ---
    "{} не найден ({}) — укажите путь к ffmpeg в настройках": "{} not found ({}) — set the ffmpeg path in settings",
    "ffprobe не смог прочитать {}: {}": "ffprobe could not read {}: {}",
    "Рендер %d%%": "Render %d%%",
    "Превью": "Preview",
    "Рендер": "Render",
    "{} кадров": "{} frames",
    "{}–{} с": "{}–{} s",
    "%s %s: %dx%d @ %.3g fps, %s, offset %g мс, кодек %s": "%s %s: %dx%d @ %.3g fps, %s, offset %g ms, codec %s",
    "рендер отменён": "render cancelled",
    "ffmpeg завершился с кодом {}:\n{}": "ffmpeg exited with code {}:\n{}",
    "Готово: %s — %dx%d @ %.3g fps, %.2f с": "Done: %s — %dx%d @ %.3g fps, %.2f s",
    "Параметры результата не совпадают с исходником (%.2f с)!": "The result does not match the source (%.2f s)!",
    "Кодировщик: %s": "Encoder: %s",
}

EN.update({
    # --- онбординг ---
    "ПРОПУСТИТЬ": "SKIP", "Далее": "Next", "Назад": "Back", "Начать работу": "Get started",
    "первая настройка": "first-time setup", "ЯЗЫК": "LANGUAGE",
    "Привет.\nНастроим?": "Hello.\nLet's set up.", "Подключим\nOBS.": "Connect\nOBS.",
    "Что\nзаписываем.": "What we\nrecord.", "Куда и чем\nсохранять.": "Where and\nhow to save.", "Готово.": "Done.",
    "Smooth Cursor записывает экран через OBS и делает курсор на видео плавным, как в Screen Studio: сглаживает "
    "дрожание, показывает клики и шлейф движения. Настройка займёт минуту.":
        "Smooth Cursor records your screen with OBS and makes the cursor in the video smooth, like Screen Studio: it "
        "removes jitter and shows clicks and a motion trail. Setup takes about a minute.",
    "1. Установите и откройте OBS Studio (версия 28 или новее).\n2. В OBS: Сервис → Настройки сервера WebSocket.\n"
    "3. Включите «Включить сервер WebSocket». Если включена аутентификация — нажмите «Показать данные для "
    "подключения» и перенесите пароль сюда.":
        "1. Install and open OBS Studio (version 28 or newer).\n2. In OBS: Tools → WebSocket Server Settings.\n"
        "3. Turn on “Enable WebSocket server”. If authentication is on, click “Show Connect Info” and copy the "
        "password here.",
    "Скачать OBS": "Download OBS", "Проверить подключение": "Test connection", "Подключаюсь…": "Connecting…",
    "Подключено: OBS {}": "Connected: OBS {}", "Не получилось: ": "Didn't work: ",
    "Выберите экран, который будете записывать, и частоту кадров. Кнопка ниже сама настроит OBS: создаст сцену "
    "«Smooth Cursor» с захватом этого экрана без курсора, выставит разрешение экрана и fps и сделает сцену текущей.":
        "Pick the screen you will record and the frame rate. The button below sets up OBS for you: it creates a "
        "“Smooth Cursor” scene capturing that screen without the cursor, sets the screen resolution and fps and "
        "makes the scene current.",
    "Кадров в секунду": "Frames per second",
    "60 — плавнее, 30 — меньше файл и нагрузка": "60 is smoother, 30 means smaller files and less load",
    "Настроить OBS": "Set up OBS", "Настраиваю OBS…": "Setting up OBS…",
    "Сначала подключите OBS — шаг 2.": "Connect OBS first — step 2.",
    "Готово: сцена «Smooth Cursor», {} · {}×{} · {} fps": "Done: scene “Smooth Cursor”, {} · {}×{} · {} fps",
    "Сюда OBS сохраняет записи, а программа — готовые видео с плавным курсором.":
        "OBS saves recordings here, and the app saves the finished smooth-cursor videos next to them.",
    "FFMPEG И ВИДЕОКАРТА": "FFMPEG AND GPU",
    "старт и стоп записи из любой программы": "start and stop recording from any app",
    "Скачиваю ffmpeg…": "Downloading ffmpeg…",
    "ffmpeg не найден — без него видео не собрать.": "ffmpeg not found — videos can't be made without it.",
    "Установить ffmpeg (≈100 МБ)": "Install ffmpeg (≈100 MB)", "ffmpeg есть, кодирует: ": "ffmpeg found, encoding with: ",
    "Нажмите {} или «Начать запись», чтобы записать экран. Когда остановите, рядом с записью появится видео с "
    "плавным курсором.":
        "Press {} or “Start recording” to record your screen. When you stop, a video with a smooth cursor appears "
        "next to the recording.",
    "Справа в окне — живое превью: двигайте мышью, чтобы увидеть, как будет выглядеть курсор. Настройки сглаживания, "
    "клика и шлейфа — во вкладках. Эту настройку можно открыть снова кнопкой «Настройка».":
        "On the right is a live preview: move your mouse to see how the cursor will look. Smoothing, click and trail "
        "settings are in the tabs. You can reopen this setup with the “Setup” button.",
    # --- главное окно ---
    "для записей OBS": "for OBS recordings", "Настройка": "Setup", "Записи": "Recordings", "Движение": "Motion",
    "Эффекты": "Effects", "Вывод": "Output", "Начать запись": "Start recording", "Остановить": "Stop",
    "Подключить OBS": "Connect OBS",
    "Записывайте как обычно — курсор на видео станет плавным сам. Справа живое превью: так курсор будет выглядеть "
    "с текущими настройками.":
        "Record as usual — the cursor in the video becomes smooth by itself. On the right is a live preview of how "
        "the cursor will look with the current settings.",
    "Рендерить сразу после записи": "Render right after recording", "СЕЙЧАС В OBS": "NOW IN OBS",
    "Обновить": "Refresh", "РЕНДЕР": "RENDER", "Отменить": "Cancel", "ЖУРНАЛ": "LOG", "Весь журнал": "Full log",
    "журнал": "log", "нет задач": "idle", "в очереди: {}": "queued: {}", "превью": "preview", "рендер": "render",
    "Превью не удалось": "Preview failed", "Рендер не удался": "Render failed",
    "ЖИВОЕ ПРЕВЬЮ": "LIVE PREVIEW", "двигайте мышью и кликайте": "move the mouse and click",
    "как двигалась рука": "how your hand moved", "курсор на видео": "cursor in the video",
    "шлейф и анимация клика": "trail and click animation", "покой": "rest", "нажатие": "press", "возврат": "release",
    "OBS — нет связи": "OBS — not connected", "OBS — подключение…": "OBS — connecting…",
    "OBS {} — подключено": "OBS {} — connected", "Сцена «{}» · экран {}": "Scene “{}” · screen {}",
    "Сцена —": "Scene —", "Холст —": "Canvas —", "{} · профиль «{}»": "{} · profile “{}”",
    "В сцене OBS нет захвата экрана": "The OBS scene has no display capture",
    "видеокарта NVIDIA (HEVC)": "NVIDIA GPU (HEVC)", "видеокарта AMD (HEVC)": "AMD GPU (HEVC)",
    "графика Intel (HEVC)": "Intel graphics (HEVC)", "процессор (H.264) — медленнее": "CPU (H.264) — slower",
    "ffmpeg не найден": "ffmpeg not found", "Авто сейчас: ": "Auto right now: ",
    "ffmpeg не найден — без него программа не соберёт видео. Скачать и установить его сейчас (≈100 МБ)?":
        "ffmpeg was not found — the app can't make videos without it. Download and install it now (≈100 MB)?",
    "ffmpeg установлен: %s": "ffmpeg installed: %s", "загрузка ffmpeg": "downloading ffmpeg",
    "не удалось скачать ffmpeg: {}": "could not download ffmpeg: {}", "загрузка отменена": "download cancelled",
    # --- записи ---
    "ПАПКА ЗАПИСЕЙ": "RECORDINGS FOLDER", "Изменить…": "Change…", "Обновить список": "Refresh list",
    "ЗАПИСЬ": "RECORDING", "ДАТА": "DATE", "ПЛАВНЫЙ КУРСОР": "SMOOTH CURSOR", "готово": "done",
    "с": "from", "сек, длиной": "s, length", "сек": "s", "В папке": "Show in folder", "Исходник": "Source",
    "Открыть": "Open", "Выберите запись в списке.": "Select a recording in the list.",
    "Эта запись ещё не отрендерена — нажмите «Рендер».": "This recording hasn't been rendered yet — press “Render”.",
    "Куда OBS будет сохранять записи": "Where OBS will save recordings",
    "Папка записей: %s — OBS переключится на неё при подключении":
        "Recordings folder: %s — OBS will switch to it when connected",
    "OBS теперь сохраняет записи в %s": "OBS now saves recordings to %s",
    "Не удалось сменить папку записей в OBS: %s": "Could not change the OBS recordings folder: %s",
    # --- движение ---
    "Метод сглаживания": "Smoothing method", "Пружина": "Spring", "Жёсткость": "Stiffness", "Демпфирование": "Damping",
    "задержка ≈ {} мс. Меньше — плавнее и медленнее, больше — быстрее догоняет руку.":
        "delay ≈ {} ms. Lower is smoother and slower, higher catches up with your hand faster.",
    "1 — без перелёта. Меньше — курсор слегка проскакивает цель, больше — вязко.":
        "1 means no overshoot. Lower makes the cursor overshoot slightly, higher feels sluggish.",
    "Мягко и «дорого», как в Screen Studio.": "Soft and “premium”, like Screen Studio.",
    "Почти без задержки, сглаживает в основном медленные движения.": "Almost no delay, smooths mostly slow movements.",
    "Мин. частота": "Min. cutoff", "Гц. Меньше — сильнее сглаживаются медленные движения.":
        "Hz. Lower smooths slow movements more.",
    "Бета": "Beta", "Больше — меньше задержка на быстрых движениях.": "Higher means less delay on fast movements.",
    "Общее": "General", "Притяжение к клику": "Snap to click",
    "мс до и после клика: курсор приходит точно в точку клика (видно на готовом видео).":
        "ms before and after a click: the cursor lands exactly on the click point (visible in the final video).",
    "Мёртвая зона": "Dead zone", "px: дрожание руки меньше этого радиуса не двигает курсор.":
        "px: hand jitter smaller than this radius doesn't move the cursor.",
    # --- эффекты ---
    "Нажатие": "Click", "включено": "on", "включён": "on", "Сжатие до": "Shrink to",
    "Доля размера в самой глубокой точке.": "Size at the deepest point of the press.",
    "Наклон": "Tilt", "Градусы против часовой вокруг кончика стрелки; минус — по часовой.":
        "Degrees counter-clockwise around the arrow tip; negative is clockwise.",
    "Длительность": "Duration",
    "мс: первая четверть — курсор вжимается, остальное время плавно возвращается. Пока кнопка зажата, наклон держится.":
        "ms: the first quarter presses in, the rest eases back. The tilt holds while the button is down.",
    "Курсор": "Cursor", "Размер": "Size",
    "1 — как в системе, с учётом масштаба экрана и масштаба захвата в сцене OBS.":
        "1 is the system size, adjusted for display scaling and the capture scale in the OBS scene.",
    "Шлейф · motion blur": "Trail · motion blur", "Длина": "Length",
    "В долях кадра: 0.5 — как у камеры с выдержкой 180°. Пока курсор стоит или идёт анимация клика, шлейф не "
    "рисуется.": "In frames: 0.5 is like a camera with a 180° shutter. No trail while the cursor rests or a click "
                "animation plays.",
    "Плотность": "Density", "Насколько заметен шлейф.": "How visible the trail is.",
    # --- вывод ---
    "Кодирование": "Encoding", "Кодек": "Codec", "Авто": "Auto", "Процессор": "CPU", "Пресет": "Preset",
    "Качество (CQ)": "Quality (CQ)",
    "Меньше — лучше и тяжелее файл; 18 — почти без потерь. Кодирует видеокарта (NVENC).":
        "Lower is better quality and bigger files; 18 is nearly lossless.",
    "Синхронизация с видео": "Sync with video", "Сдвиг · 30 fps": "Offset · 30 fps", "Сдвиг · 60 fps": "Offset · 60 fps",
    "Другой fps": "Other fps",
    "мс. Курсор опережает картинку — уменьшите, отстаёт — увеличьте. Проверять удобно с отладкой.":
        "ms. Cursor ahead of the picture — decrease; behind — increase. Debug mode helps to check.",
    "Дополнительно": "Extras", "Отладка: поверх — реальный курсор (красный)": "Debug: draw the real cursor on top (red)",
    "Траектория в CSV (Fusion / After Effects)": "Path to CSV (Fusion / After Effects)", "Обзор…": "Browse…",
    "Все": "All files",
    # --- OBS ---
    "Подключение": "Connection", "Адрес": "Host", "Порт": "Port", "Пароль": "Password", "Подключиться": "Connect",
    "OBS → Сервис → Настройки сервера WebSocket": "OBS → Tools → WebSocket Server Settings",
    "Что записывать": "What to record", "Авто — по сцене OBS": "Auto — from the OBS scene", "АВТО": "AUTO",
    "Выключать захват курсора в источниках сцены": "Turn off cursor capture in scene sources",
    "Горячая клавиша": "Hotkey", "ГОРЯЧАЯ КЛАВИША": "HOTKEY", "Старт и стоп": "Start / stop", "Применить": "Apply",
    "Горячая клавиша: %s": "Hotkey: %s", "Непонятная горячая клавиша: {}": "Unrecognized hotkey: {}",
    "Идёт запись — сначала остановите её.": "Recording in progress — stop it first.",
    "Идёт запись. Остановить её и выйти?": "Recording in progress. Stop it and quit?",
    "Идёт рендер. Прервать и выйти?": "Rendering in progress. Cancel and quit?", "Ошибка: %s": "Error: %s",
})
