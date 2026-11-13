"""Перевод интерфейса и сообщений: ключ — русская строка, значение — английская. t(s) возвращает строку
на текущем языке (LANG = "ru" | "en"); неизвестные строки остаются как есть."""
LANG = "ru"


def t(s):
    return EN.get(s, s) if LANG == "en" else s


EN = {
    # --- Linux ---
    "Нет библиотек X11 ({}). Поставьте libx11-6, libxfixes3 и libxrandr2":
        "X11 libraries are missing ({}). Install libx11-6, libxfixes3 and libxrandr2",
    "Не могу подключиться к X-серверу. На Wayland нужен XWayland": "Cannot reach the X server. Wayland needs XWayland",
    "В этой сборке OBS нет захвата экрана через PipeWire": "This OBS build has no PipeWire screen capture",
    "Нет доступа к /dev/input. Выполните: sudo usermod -aG input $USER — и войдите заново":
        "No access to /dev/input. Run: sudo usermod -aG input $USER — then log in again",
    "Портал экрана: {}": "Screen portal: {}",
    "Экран для записи курсора не выбран": "No screen was chosen for cursor recording",
    "Этот рабочий стол не отдаёт положение курсора (нужны GNOME или KDE на Wayland). Войдите в сеанс X11":
        "This desktop does not report the cursor position (Wayland needs GNOME or KDE). Log in to an X11 session",
    "Нет библиотеки PipeWire ({}). Поставьте libpipewire-0.3-0": "PipeWire library is missing ({}). Install libpipewire-0.3-0",
    "Не удалось подключиться к PipeWire": "Could not connect to PipeWire",
    "Для курсора выбран экран %dx%d, а OBS снимает %dx%d — выберите тот же экран":
        "The cursor stream uses a %dx%d screen but OBS captures %dx%d — choose the same screen",
    "Поток экрана прервался: %s": "The screen stream stopped: %s",
    "На этом рабочем столе нет трея: крестик закрывает программу":
        "This desktop has no system tray: the close button quits the app",
    "Поставьте ffmpeg из репозитория своего дистрибутива, например: sudo apt install ffmpeg":
        "Install ffmpeg from your distribution, for example: sudo apt install ffmpeg",
    # --- запись и OBS ---
    "Подключился к OBS %s (websocket %s)": "Connected to OBS %s (websocket %s)",
    "Выключил захват курсора в источнике «%s»": "Turned off cursor capture in source “%s”",
    "В источнике «%s» включён захват курсора — на видео будет два курсора":
        "Cursor capture is on in source “%s” — the video will show two cursors",
    "Курсор попадает в кадр «%s», хотя захват курсора выключен — на видео будет два курсора":
        "The cursor shows up in “%s” even with cursor capture off — the video will show two cursors",
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
    "пауза": "paused",
    "Не удалось поставить запись на паузу: %s": "Could not pause the recording: %s",
    "Значок записи в углу экрана": "Recording badge in the screen corner", "Значок записи": "Recording badge",
    "в углу экрана: идёт ли запись, пауза и стоп; перетаскивается мышью. На видео его нет.":
        "in the screen corner: whether recording is on, pause and stop; drag it anywhere. It is not in the video.",
    "Windows не умеет прятать значок записи от захвата экрана (нужна Windows 10 2004 или новее)":
        "Windows can't hide the recording badge from screen capture (needs Windows 10 2004 or newer)",
    "Windows рисует курсор прямо в картинку экрана (крупный указатель или масштаб экрана) — захват экрана переключён "
    "на метод «Windows 10 (1903 и новее)», иначе курсор попал бы в видео":
        "Windows draws the cursor into the screen image (large pointer or display scale) — Display Capture switched to "
        "the “Windows 10 (1903 and up)” method, otherwise the cursor would end up in the video",
    "OBS настроен: сцена «%s», %s %dx%d @ %d fps": "OBS is set up: scene “%s”, %s %dx%d @ %d fps",
    "Не удалось остановить запись в OBS: %s": "Could not stop the recording in OBS: %s",
    "■ Запись остановлена: %s": "■ Recording stopped: %s",
    "OBS не сообщил имя файла записи — лог для него не сохранён":
        "OBS did not report the recording file name — no cursor log was saved for it",
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
    "{} открыт в другой программе — новый рендер сохранён как {}": "{} is open in another app — the new render was saved as {}",
    "ffmpeg завершился с кодом {}:\n{}": "ffmpeg exited with code {}:\n{}",
    "Готово: %s — %dx%d @ %.3g fps, %.2f с": "Done: %s — %dx%d @ %.3g fps, %.2f s",
    "Параметры результата не совпадают с исходником (%.2f с)!": "The result does not match the source (%.2f s)!",
    "Видеокарта отдала пустые кадры — рендерю заново без аппаратного декодирования":
        "The GPU returned blank frames — rendering again without hardware decoding",
    "Кодировщик: %s": "Encoder: %s",
}

EN.update({
    # --- онбординг ---
    "Далее": "Next", "Назад": "Back", "Начать работу": "Get started",
    "первая настройка": "first-time setup", "ЯЗЫК": "LANGUAGE", "Закрыть": "Close",
    "Привет.\nНастроим?": "Hello.\nLet's set up.", "Подключим\nOBS.": "Connect\nOBS.",
    "Что\nзаписываем.": "What we\nrecord.", "Куда и чем\nсохранять.": "Where and\nhow to save.", "Готово.": "Done.",
    "Smooth Cursor записывает экран через OBS и делает курсор на видео плавным, как в Screen Studio: сглаживает "
    "дрожание, показывает клики и шлейф движения. Настройка займёт минуту.":
        "Smooth Cursor records your screen with OBS and makes the cursor in the video smooth, like Screen Studio: it "
        "removes jitter and shows clicks and a motion trail. Setup takes about a minute.",
    "1. Установите и откройте OBS Studio (версия 30 или новее).\n2. В OBS: Сервис → Настройки сервера WebSocket.\n"
    "3. Включите «Включить сервер WebSocket». Если включена аутентификация — нажмите «Показать данные для "
    "подключения» и перенесите пароль сюда.":
        "1. Install and open OBS Studio (version 30 or newer).\n2. In OBS: Tools → WebSocket Server Settings.\n"
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
    "Проверить": "Refresh", "РЕНДЕР": "RENDER", "Отменить": "Cancel", "ЖУРНАЛ": "LOG", "Весь журнал": "Full log",
    "журнал": "log", "нет задач": "idle", "в очереди: {}": "queued: {}", "превью": "preview", "рендер": "render",
    "Превью не удалось": "Preview failed", "Рендер не удался": "Render failed",
    "ЖИВОЕ ПРЕВЬЮ": "LIVE PREVIEW", "двигайте мышью и кликайте": "move the mouse and click",
    "как двигалась рука": "how your hand moved", "курсор на видео": "cursor in the video",
    "шлейф и анимация клика": "trail and click animation", "покой": "rest", "нажатие": "press", "возврат": "release",
    "OBS — нет связи": "OBS — not connected", "OBS — подключение…": "OBS — connecting…",
    "OBS {} — подключено": "OBS {} — connected", "Сцена «{}» · экран {}": "Scene “{}” · screen {}",
    "Сцена —": "Scene —", "Холст —": "Canvas —", "{} · профиль «{}»": "{} · profile “{}”",
    "Исправить": "Fix", "OBS — запускается…": "OBS — starting…",
    "Запускаю OBS — подключусь, когда он загрузится": "Starting OBS, will connect once it loads",
    "Профиль": "Profile", "Файл .mov; качество задаёт профиль.": ".mov file; quality is set by the profile.", "Битрейт записи {:g} Мбит/с — мало, при зуме будет мыло":
        "Recording bitrate {:g} Mbps is too low: zooming in will look blurry",
    "Переключить запись OBS на постоянное качество (CQ 18)? Битрейт будет подстраиваться сам, файлы станут крупнее."
    "\n\nЗакройте окно настроек OBS, если оно открыто, — иначе оно вернёт старые значения.":
        "Switch OBS recording to constant quality (CQ 18)? Bitrate will adapt on its own, files get bigger."
        "\n\nClose the OBS settings window if it's open, or it will restore the old values.",
    "Не удалось поменять качество записи OBS: %s": "Could not change OBS recording quality: %s",
    "Запись OBS переключена на постоянное качество (CQ 18), профиль «%s» перезагружен":
        "OBS recording switched to constant quality (CQ 18), profile “%s” reloaded",
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
    "Не удалось сменить папку записей в OBS (нужен OBS 30 или новее): %s":
        "Could not change the OBS recordings folder (OBS 30 or newer is needed): %s",
    # --- движение ---
    "Метод сглаживания": "Smoothing method", "Пружина": "Spring", "Жёсткость": "Stiffness", "Демпфирование": "Damping",
    "сглаживание ≈ {} мс: меньше — плавнее, больше — точнее повторяет руку. Задержки нет.":
        "smoothing ≈ {} ms: lower is smoother, higher follows your hand more closely. No delay.",
    "1 — без перелёта. Меньше — курсор слегка проскакивает цель, больше — вязко.":
        "1 means no overshoot. Lower makes the cursor overshoot slightly, higher feels sluggish.",
    "Мягко и «дорого», как в Screen Studio.": "Soft and “premium”, like Screen Studio.",
    "Обновление": "Update", "Вышла\nверсия {}.": "Version {}\nis out.", "Обновить": "Update",
    "Не сейчас": "Not now", "Скачиваю…": "Downloading…", "Ещё раз": "Retry",
    "Программа закроется и через пару секунд откроется уже обновлённой. Настройки и записи останутся на месте.":
        "The app will close and reopen updated in a couple of seconds. Your settings and recordings stay as they are.",
    "Обновление пропущено. Скачать можно здесь: %s": "Update skipped. You can download it here: %s",
    "Скачиваю обновление %s…": "Downloading update %s…",
    "Не удалось обновиться: %s": "Update failed: %s",
    "файл скачался с ошибкой (не совпала контрольная сумма)": "the download is corrupted (checksum mismatch)",
    "Пресет": "Preset", "Стандарт": "Standard", "Лёгкий": "Light", "Кино": "Cinematic", "Без эффектов": "No effects",
    "С наклоном": "Lean", "Наклон v2": "Lean v2", "Мой": "Mine", "СВОИ ПРЕСЕТЫ": "YOUR PRESETS", "удалить?": "delete?",
    "Сохранить": "Save",
    "Запомнит текущие сглаживание и эффекты.\nС тем же именем — обновит этот пресет.":
        "Saves the current smoothing and effects.\nThe same name updates that preset.",
    "свой": "custom", "Меняет сглаживание и эффекты.\nДальше можно подстроить ползунками.":
        "Sets smoothing and effects.\nFine-tune with the sliders afterwards.",
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
    "Смена курсора": "Cursor change", "Отскок": "Bounce",
    "На сколько новый вид проскакивает крупнее, прежде чем встать на место. 0 — без отскока.":
        "How much bigger the new cursor overshoots before it settles. 0 — no bounce.", "Кино + смена курсора": "Cinematic + cursor change",
    "мс: прежний вид сжимается в точку, новый вырастает из неё с отскоком.":
        "ms: the old shape shrinks to a point, the new one grows out of it with a bounce.",
    "мс: первая четверть — нажатие, затем плавный возврат. Пока кнопка зажата, наклон держится.":
        "ms: the first quarter presses in, the rest eases back. The tilt holds while the button is down.",
    "СХЕМЫ WINDOWS": "WINDOWS SCHEMES",
    "СВОЙ КУРСОР": "YOUR CURSOR",
    "Не менять на миг": "Ignore flickers", "Порог": "Threshold",
    "мс": "ms", "Гц": "Hz",
    "Когда курсор меняет вид — например, над ссылкой стрелка становится рукой, — новый вид плавно "
    "вырастает из старого, а не появляется рывком.":
        "When the cursor changes shape — say, the arrow turns into a hand over a link — the new shape smoothly "
        "grows out of the old one instead of popping in.",
    "Курсор лишь проскочил над кнопкой — вид на видео не меняется, без лишнего мелькания. "
    "В живом превью смена из-за этого чуть запаздывает, в видео — нет.":
        "The cursor just slipped over a button — its shape in the video stays, no needless flicker. "
        "Because of this the live preview switches a bit late; the video doesn't.",
    "стрелка → рука или текст: новый вид вырастает из прежнего, а не появляется сразу.":
        "arrow → hand or text: the new shape grows out of the old one instead of popping in.",
    "Задержка — только в превью": "The delay is only in the preview", "Понятно": "Got it",
    "Если курсор пролетел кнопку и стал рукой на долю секунды, смены вида\nне будет — без лишней "
    "анимации.\n\nВ живом превью новый вид появляется с задержкой на порог: программа\nне знает "
    "заранее, надолго ли он. В видео задержки нет — при рендере\nвидна вся запись: короткие "
    "мелькания просто пропадают, остальные\nсмены анимируются вовремя.":
        "If the cursor flies over a button and turns into a hand for a split second,\nthe shape "
        "will not change — no extra animation.\n\nIn the live preview a new shape appears after the "
        "threshold delay:\nthe app cannot know in advance how long it will last. The video has\nno "
        "delay — rendering sees the whole recording: brief flickers just disappear,\nother changes "
        "animate on time.",
    "вид, мелькнувший короче (пролетели кнопку), не меняется. Превью — с задержкой, видео — без.":
        "a shape shown for less than this (flew over a button) is skipped. Preview: delayed, video: not.",
    "Свой файл станет стрелкой, остальные виды —\nиз файлов рядом с ним по именам.":
        "Your file becomes the arrow, other shapes —\nfrom files next to it, by name.",
    "Курсоры программ": "App cursors", "Вид": "Look", "Как в системе": "As in the system",
    "Свой файл .cur…": "Own .cur file…", "Курсор-стрелка": "Arrow cursor",
    "инструменты Premiere, Figma и т. п.: увеличьте, если рядом с вашим курсором они мелкие.":
        "Premiere, Figma and other app tools: scale up if they look tiny next to your cursor.",
    "каким курсором рисовать видео, и после записи тоже. Свой .cur — стрелка, остальное — рядом.":
        "which cursor the video uses, even after recording. Own .cur — the arrow, rest from its folder.",
    "Курсор": "Cursor", "Размер": "Size", "Наклон в движении": "Lean in motion", "Вправо": "Right", "Влево": "Left",
    "поворот, когда курсор едет вправо. 45° — зеркально покою, минус — назад, 0 — выключено.":
        "lean while moving right. 45° mirrors the resting pose, minus leans back, 0 is off.",
    "поворот, когда курсор едет влево. Минус — назад, как от инерции, 0 — выключено.":
        "lean while moving left. Minus leans back, as if from inertia, 0 is off.",
    "1 — как в системе, с учётом масштаба экрана и масштаба захвата в сцене OBS.":
        "1 is the system size, adjusted for display scaling and the capture scale in the OBS scene.",
    "Шлейф · motion blur": "Trail · motion blur", "Длина": "Length",
    "В долях кадра: 0.5 — как у камеры с выдержкой 180°. В покое и при анимации клика шлейфа нет.": "In frames: 0.5 is like a camera with a 180° shutter. No trail while the cursor rests or a click "
                "animation plays.",
    "Плотность": "Density",
    "Насколько заметен шлейф. В точном режиме не используется.":
        "How visible the trail is. Not used in accurate mode.",
    "Точный, как у камеры — рендер дольше": "Accurate, like a camera — slower render",
    "Точный motion blur": "Accurate motion blur", "точный блюр": "accurate blur",
    "Точный": "Accurate", "Выдержка": "Shutter",
    "как у камеры: курсор смазан за это время. Для «киношного» вида — вдвое короче кадра: 1/60 при 30 fps.":
        "like a camera: the cursor is smeared over this time. For a film look, half a frame: 1/60 at 30 fps.",
    "смаз по всей выдержке вместо копий. Цена: рендер до 1,6× дольше, шлейф бледнее, превью упрощённое.":
        "smear over the whole shutter instead of copies. Cost: up to 1.6× slower render, fainter trail, "
        "simplified preview.",
    "Как у камеры: курсор смазан по всей выдержке,\nа не нарисован копиями. Цена: на быстрых рывках\nрендер до "
    "1,6× дольше, шлейф бледнее копий.":
        "Like a camera: the cursor is smeared over the whole\nshutter instead of drawn as copies. Cost: up to 1.6×\n"
        "slower render on fast moves, a fainter trail.",
    "Точный motion blur: выдержка 1/%d с, слой %dx%d px": "Accurate motion blur: shutter 1/%d s, layer %dx%d px",
    # --- вывод ---
    "Кодирование": "Encoding", "Кодек": "Codec", "Авто": "Auto", "Процессор": "CPU", "Пресет": "Preset",
    "Качество (CQ)": "Quality (CQ)",
    "Меньше — лучше и тяжелее файл; 18 — почти без потерь.":
        "Lower is better quality and bigger files; 18 is nearly lossless.",
    "Дополнительно": "Extras", "Отладка: поверх — реальный курсор (красный)": "Debug: draw the real cursor on top (red)",
    "Траектория в CSV (для DaVinci Fusion)": "Path to CSV (for DaVinci Fusion)", "Обзор…": "Browse…",
    "Все": "All files",
    # --- OBS ---
    "Подключение": "Connection", "Адрес": "Host", "Порт": "Port", "Пароль": "Password", "Подключиться": "Connect",
    "OBS → Сервис → Настройки сервера WebSocket": "OBS → Tools → WebSocket Server Settings",
    "Что записывать": "What to record", "Авто — по сцене OBS": "Auto — from the OBS scene", "АВТО": "AUTO",
    "Выключать захват курсора в источниках сцены": "Turn off cursor capture in scene sources",
    "Проверять перед записью, что курсора нет в кадре": "Check before recording that the cursor is not in the frame",
    "Проверка курсора": "Cursor check",
    "не попал ли курсор в кадр OBS (бывает на 4K); если да — захват «Windows 10». Раз на экран.":
        "whether the cursor got into the OBS frame (happens on 4K); if so — «Windows 10» capture. Once per screen.",
    "Горячая клавиша": "Hotkey", "ГОРЯЧАЯ КЛАВИША": "HOTKEY", "Старт и стоп": "Start / stop", "Применить": "Apply",
    "Горячая клавиша: %s": "Hotkey: %s", "Непонятная горячая клавиша: {}": "Unrecognized hotkey: {}",
    "Идёт запись — сначала остановите её.": "Recording in progress — stop it first.",
    "Идёт запись. Остановить её и выйти?": "Recording in progress. Stop it and quit?",
    "Идёт рендер. Прервать и выйти?": "Rendering in progress. Cancel and quit?", "Ошибка: %s": "Error: %s",
    # --- After Effects ---
    "Импорт в открытую композицию": "Import into the open composition",
    "Запись и курсор лягут отдельными слоями в композицию,\nкоторая сейчас открыта в After Effects — поверх\nтого, "
    "что в ней уже есть, от текущего времени.":
        "The recording and the cursor go as separate layers into\nthe composition open in After Effects — on top\nof "
        "what is already there, at the current time.",
    "…ваши слои": "…your layers",
    "Курсор — картинка с ключами прямо на ней: на остановках, кликах и поворотах пути, их удобно править.\nОткройте нужную "
    "композицию в AE и нажмите «Импортировать».\nНет открытой композиции — создадим новую по размеру и fps записи."
    "\nРазмытие в движении — средствами AE: включим его у слоёв курсора, затвор как в программе\nподскажем в журнале.":
        "The cursor is a picture with keys right on it, at stops, clicks and turns of the path — easy to edit.\nOpen the "
        "composition you need in AE and press “Import”.\nNo composition open — we create one with the recording's "
        "size and fps.\nMotion blur is done by AE: we turn it on for the cursor layers, and the log shows\nthe "
        "shutter that matches the app.",
    "Это окно показывается один раз.": "This window shows only once.", "Импортировать": "Import", "Отмена": "Cancel",
    "After Effects: размытие включено у слоёв курсора и композиции. Как в программе — Composition Settings → "
    "Advanced: Shutter Angle %d°, Shutter Phase %d°":
        "After Effects: motion blur is on for the cursor layers and the composition. To match the app — "
        "Composition Settings → Advanced: Shutter Angle %d°, Shutter Phase %d°",
    "After Effects: %s": "After Effects: %s",
    "Перепаковываю %s в mp4 для After Effects…": "Remuxing %s to mp4 for After Effects…",
    "ffmpeg не смог перепаковать {}: {}": "ffmpeg could not remux {}: {}",
    "After Effects: %d ключей позиции на %d кадров, слои в %s": "After Effects: %d position keys for %d frames, "
                                                                "layers in %s",
    "After Effects не найден — установлен ли он?": "After Effects not found — is it installed?",
    "У этой записи в композиции курсор уже есть. Чтобы заменить, удалите слой «Smooth Cursor» и нажмите ещё раз.":
        "This recording already has a cursor in the composition. To replace it, delete the “Smooth Cursor” layer and "
        "press again.",
    "В композиции есть записи, которые Smooth Cursor ещё не готовил для After Effects. Выберите их в программе и "
    "нажмите «→ After Effects» ещё раз:": "The composition has recordings Smooth Cursor has not prepared for After "
                                         "Effects yet. Select them in the app and press “→ After Effects” again:",
    # --- Premiere ---
    "Premiere: видео в секвенции «%s» с %s (V1)": "Premiere: video placed in sequence “%s” at %s (V1)",
    "Premiere не вставил видео: %s": "Premiere did not place the video: %s",
    "Premiere: жду, когда заберёт %s…": "Premiere: waiting for it to take %s…",
    "Premiere пока не забрал видео: он открыт? Не помогает — перезапустите Premiere, видео дождётся.":
        "Premiere has not taken the video yet: is it open? If that doesn't help, restart Premiere — the video will "
        "wait.",
    "Premiere открыт, но плагин не отвечает — ставлю его заново…":
        "Premiere is open but the plugin does not answer — reinstalling it…",
    "Нет установщика плагинов Adobe — установлен ли Creative Cloud?":
        "Adobe's plugin installer is missing — is Creative Cloud installed?",
    "установщик Adobe: {}": "Adobe installer: {}",
    "Плагин Smooth Cursor для Premiere установлен": "Smooth Cursor plugin for Premiere installed",
    "Чтобы класть видео в секвенцию, в Premiere нужен плагин Smooth Cursor — он ставится один раз установщиком "
    "Adobe.\n\nПоставить? Видео ляжет в открытую секвенцию на плейхед, а если там занято — сразу после, где хватит "
    "места (V1 и A1).":
        "To place videos into a sequence, Premiere needs the Smooth Cursor plugin — Adobe's installer adds it once."
        "\n\nInstall it? The video goes into the open sequence at the playhead, or right after it where there is "
        "room if that spot is taken (V1 and A1).",
    "Плагин для Premiere не поставился: %s": "Premiere plugin was not installed: %s",
    # --- запуск с OBS, трей ---
    "Запуск": "Startup",
    "Запускать вместе с OBS": "Start together with OBS",
    "Работать в трее: с OBS — без окна, крестик сворачивает": "Live in the tray: no window with OBS, X minimizes",
    "Не удалось настроить запуск вместе с OBS: %s": "Could not set up starting with OBS: %s",
    "Открыть Smooth Cursor": "Open Smooth Cursor",
    "Остановить запись": "Stop recording",
    "Выход": "Exit",
    "OBS открыт, но не подключается. Включён ли WebSocket?": "OBS is open but won't connect. Is WebSocket enabled?",
    "● Запись идёт (начата в OBS)": "● Recording (started in OBS)",
    # --- виртуальный монитор ---
    "Виртуальный монитор": "Virtual monitor",
    "Виртуальный монитор: %s": "Virtual monitor: %s",
    "Включить": "Turn on",
    "Выключить": "Turn off",
    "Показать экран": "Show screen",
    "Разрешение": "Resolution",
    "Включён: {} · {}×{}. Уведите курсор за край экрана — окно OBS покажет, что там.":
        "On: {} · {}×{}. Move the cursor past the screen edge — the OBS window shows what is there.",
    "Монитора нет в Windows — выключите и включите снова.": "Windows has no such monitor — turn it off and on again.",
    "Отдельный экран только для записи: ваш монитор остаётся свободным. Включите — драйвер, OBS и окно с этим экраном "
    "настроятся сами.":
        "A separate screen just for recording: your monitor stays free. Turn it on — the driver, OBS and a window "
        "showing this screen set themselves up.",
    "Откройте OBS — окно с экраном появится само.": "Open OBS — the window with the screen appears by itself.",
    "OBS не открыл окно проектора": "OBS did not open the projector window",
    "Скачиваю драйвер…": "Downloading the driver…",
    "Ставлю драйвер — подтвердите запрос Windows…": "Installing the driver — confirm the Windows prompt…",
    "Включаю драйвер — подтвердите запрос Windows…": "Enabling the driver — confirm the Windows prompt…",
    "Запускаю драйвер…": "Starting the driver…",
    "Включаю монитор…": "Turning the monitor on…",
    "Windows не ответила за 5 минут": "Windows did not respond within 5 minutes",
    "Windows не дала прав администратора — запрос отклонён": "Windows gave no administrator rights — the prompt was declined",
    "драйвер не запустился — возможно, нужно перезагрузить компьютер":
        "the driver did not start — the computer may need a restart",
    "монитор не появился": "the monitor did not appear",
    "монитор не выключился": "the monitor did not turn off",
    "Частота, Гц": "Refresh rate, Hz",
    "считаю время…": "estimating…",
    "почти готово": "almost done",
    "осталось ≈ {} с": "≈ {} s left",
    "осталось ≈ {} мин {} с": "≈ {} min {} s left",
    "осталось ≈ {} мин": "≈ {} min left",
    " · ещё {}": " · {} more",
    "открыть видео ›": "open video ›",
    "подробности в журнале": "details in the log",
    "Как\nработать.": "How to\nwork.",
    "В трее — запись кнопкой в OBS": "In the tray — record with the button in OBS",
    "Программа сама запускается вместе с OBS и ждёт у часов. Нажимаете «Начать запись» в OBS — она подхватит запись и "
    "покажет значок с паузой и стопом.":
        "The app starts with OBS and waits by the clock. Press Start Recording in OBS — it picks the recording up and "
        "shows a badge with pause and stop.",
    "Обычное окно — запись кнопкой здесь": "Regular window — record with the button here",
    "Открываете Smooth Cursor сами, когда нужно, и нажимаете «Начать запись» в нём или горячую клавишу.":
        "Open Smooth Cursor yourself when needed and press Start recording in it, or the hotkey.",
    "Поменять можно потом: вкладка OBS → «Запуск».": "You can change this later: OBS tab → Startup.",
    "Откройте OBS и нажмите в нём «Начать запись» — программа запустится сама. Когда остановите, рядом с записью "
    "появится видео с плавным курсором. Значок программы — у часов.":
        "Open OBS and press Start Recording there — the app starts by itself. When you stop, a video with the smooth "
        "cursor appears next to the recording. The app's icon is by the clock.",
    "Напишите ширину и высоту, например 5120x2160": "Type width and height, for example 5120x2160",
    "Разрешение — от {}×{} до {}×{}": "Resolution must be from {}×{} to {}×{}",
    "Добавляю разрешение — подтвердите запрос Windows…": "Adding the resolution — confirm the Windows prompt…",
    "драйвер не принял разрешение {} {} Гц": "the driver did not accept {} at {} Hz",
    "Windows не приняла разрешение {} {} Гц": "Windows did not accept {} at {} Hz",
    "Идёт запись — разрешение сменится при следующем включении монитора.":
        "Recording — the resolution changes the next time the monitor is turned on.",
})
