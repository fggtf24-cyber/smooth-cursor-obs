"""Установщик Smooth Cursor: один exe, без прав администратора.
Ставит программу в %LOCALAPPDATA%\\Programs\\Smooth Cursor, делает ярлыки в «Пуске» и на рабочем столе, запись
в «Установка и удаление программ». С ключом --uninstall удаляет всё обратно (кроме настроек и записей)."""
import ctypes
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import winreg
import zipfile

import yaml
from pathlib import Path
from tkinter import font as tkfont

APP = "Smooth Cursor"
EXE = "Smooth Cursor.exe"
BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
TARGET = Path(os.environ["LOCALAPPDATA"]) / "Programs" / APP


def shell_folder(csidl):
    """Настоящий путь системной папки: «Рабочий стол» бывает перенесён (OneDrive, другой диск)."""
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
    return Path(buf.value)


START_MENU = shell_folder(0x02)  # CSIDL_PROGRAMS
DESKTOP = shell_folder(0x10)     # CSIDL_DESKTOPDIRECTORY
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\SmoothCursor"
# палитра программы (app.pyw)
BG, HOVER, LINE, RULE = "#E6E6E6", "#D6D6D6", "#BDBDBD", "#D0D0D0"
MUTE, INK2, INK, RED = "#8A8A8A", "#3A3A3A", "#1C1C1C", "#D0342C"
ctypes.windll.shcore.SetProcessDpiAwareness(2)  # чёткий текст на мониторах с масштабом
CONFIG = Path(os.environ["APPDATA"]) / "SmoothCursor" / "config.yaml"  # тот же путь, что settings.PATH
LANG = "en"  # язык спрашиваем первым экраном (по-английски); при удалении — берём из настроек программы
TXT = {
    "sub": ("плавный курсор для записей OBS", "smooth cursor for OBS recordings"),
    "install": ("Установить", "Install"), "remove": ("Удалить", "Uninstall"), "run": ("Запустить", "Launch"),
    "desktop": ("Ярлык на рабочем столе", "Desktop shortcut"), "where": ("Папка: ", "Folder: "),
    "copying": ("Устанавливаю…", "Installing…"), "done": ("Готово. Smooth Cursor установлен.", "Done. Smooth Cursor is installed."),
    "removed": ("Smooth Cursor удалён. Настройки и записи остались на месте.",
                "Smooth Cursor was removed. Your settings and recordings were kept."),
    "running": ("Закройте Smooth Cursor и нажмите ещё раз.", "Close Smooth Cursor and try again."),
    "head_i": ("Установка", "Install"), "head_u": ("Удаление", "Uninstall"),
}


def tr(key):
    return TXT[key][0 if LANG == "ru" else 1]


def read_cfg():
    try:
        return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def save_lang(code):
    """Передаём выбранный язык программе: она читает ui.lang из своего config.yaml."""
    cfg = read_cfg()
    cfg.setdefault("ui", {})["lang"] = code
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")


def shortcut(path, target, icon):
    """Ярлык .lnk через встроенный в Windows COM-объект WScript.Shell."""
    q = lambda p: "'" + str(p).replace("'", "''") + "'"  # апостроф в имени пользователя (O'Neil)
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({q(path)});$s.TargetPath={q(target)};"
          f"$s.WorkingDirectory={q(Path(target).parent)};$s.IconLocation={q(icon)};$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                   creationflags=subprocess.CREATE_NO_WINDOW, check=True)


def install(desktop, progress):
    with zipfile.ZipFile(BASE / "payload.zip") as z:
        files = z.infolist()
        if TARGET.exists():
            shutil.rmtree(TARGET / "_internal", ignore_errors=True)
        for i, f in enumerate(files):
            z.extract(f, TARGET)
            progress((i + 1) * 90 / len(files))
    shutil.copy2(sys.executable, TARGET / "Uninstall.exe")
    save_lang(LANG)
    exe = TARGET / EXE
    shortcut(START_MENU / f"{APP}.lnk", exe, exe)
    if desktop:
        shortcut(DESKTOP / f"{APP}.lnk", exe, exe)
    size_kb = sum(p.stat().st_size for p in TARGET.rglob("*") if p.is_file()) // 1024
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        for name, value in (("DisplayName", APP), ("Publisher", APP), ("DisplayIcon", str(exe)),
                            ("InstallLocation", str(TARGET)), ("UninstallString", f'"{TARGET / "Uninstall.exe"}" --uninstall'),
                            ("DisplayVersion", "1.0")):
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, size_kb)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
    progress(100)


def uninstall():
    for lnk in (START_MENU / f"{APP}.lnk", DESKTOP / f"{APP}.lnk"):
        lnk.unlink(missing_ok=True)
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass
    # сам себя удалить нельзя, пока запущен, — папку стирает cmd через пару секунд после выхода
    subprocess.Popen(f'cmd /c timeout /t 2 /nobreak >nul & rmdir /s /q "{TARGET}"',
                     creationflags=subprocess.CREATE_NO_WINDOW, shell=True)


def app_running():
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {EXE}"], capture_output=True, text=True,
                         creationflags=subprocess.CREATE_NO_WINDOW).stdout
    return EXE.lower() in out.lower()


# ---------- виджеты как в программе (app.pyw): прямые углы, пиксели ----------
class Btn(tk.Canvas):
    """Кнопка-прямоугольник: primary — чёрная, outline — контур, seg — ячейка (выбранная — чёрная)."""

    def __init__(self, parent, text, command, font, kind="primary"):
        super().__init__(parent, height=32, bg=BG, highlightthickness=0, bd=0, cursor="hand2")
        self.font, self.kind, self.command = font, kind, command
        self.enabled, self.selected, self.hover = True, False, False
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<ButtonRelease-1>", lambda e: self.enabled and self.command())
        self.set_text(text)

    def _hover(self, on):
        self.hover = on
        self.draw()

    def set_text(self, text):
        self.text = text
        self.w = tkfont.Font(font=self.font).measure(text) + 32
        self.config(width=self.w)
        self.draw()

    def set_enabled(self, on):
        self.enabled = on
        self.config(cursor="hand2" if on else "arrow")
        self.draw()

    def draw(self):
        if not self.enabled:
            fill, outline, fg = LINE, LINE, BG
        elif self.kind == "primary":
            fill = INK2 if self.hover else INK
            outline, fg = fill, BG
        elif self.selected:
            fill, outline, fg = INK, INK, BG
        else:
            fill, outline, fg = (HOVER if self.hover else BG), (INK if self.kind == "outline" else LINE), INK
        self.delete("all")
        self.create_rectangle(0, 0, self.w - 1, 31, fill=fill, outline=outline)
        self.create_text(self.w / 2, 16, text=self.text, fill=fg, font=self.font)


class Toggle(tk.Canvas):
    """Пиксельный флажок: пустой квадрат / чёрный квадрат с окошком."""

    def __init__(self, parent, value):
        super().__init__(parent, width=16, height=16, bg=BG, highlightthickness=0, cursor="hand2")
        self.value = value
        self.bind("<Button-1>", lambda e: self.flip())
        self.draw()

    def flip(self):
        self.value = not self.value
        self.draw()

    def draw(self):
        self.delete("all")
        self.create_rectangle(0, 0, 15, 15, fill=INK if self.value else BG, outline=INK)
        if self.value:
            self.create_rectangle(5, 5, 10, 10, fill=BG, outline=BG)


class Window:
    def __init__(self, mode):
        self.mode, self.done = mode, False
        for f in (BASE / "fonts").glob("*.ttf"):  # Inter и Plex Mono из комплекта — только для этого процесса
            ctypes.windll.gdi32.AddFontResourceExW(str(f), 0x10, 0)
        self.root = r = tk.Tk()
        r.tk.call("tk", "scaling", 96 / 72)
        r.title(APP)
        r.configure(bg=BG)
        r.resizable(False, False)
        w, h = 720, 460
        r.geometry(f"{w}x{h}+{(r.winfo_screenwidth() - w) // 2}+{(r.winfo_screenheight() - h) // 3}")
        if (BASE / "icon.ico").exists():
            r.iconbitmap(default=str(BASE / "icon.ico"))
        fams = set(tkfont.families())
        pick = lambda *names: next((n for n in names if n in fams), names[-1])
        sans, sans_m = pick("Suisse Intl", "Inter", "Segoe UI"), pick("Suisse Intl Medium", "Inter Medium", "Segoe UI")
        self.F = dict(head=(sans, -52), body=(sans, 10), brand=(sans_m, 10), btn=(sans_m, 10),
                      mono=(pick("SF Mono", "IBM Plex Mono", "Consolas"), 9))

        page = tk.Frame(r, bg=BG)
        page.pack(fill="both", expand=True, padx=56, pady=(40, 32))
        top = tk.Frame(page, bg=BG)
        top.pack(fill="x")
        if (BASE / "icon.png").exists():
            self.logo = tk.PhotoImage(file=str(BASE / "icon.png")).subsample(8, 8)
            tk.Label(top, image=self.logo, bg=BG, bd=0).pack(side="left", padx=(0, 12))
        brand = tk.Frame(top, bg=BG)
        brand.pack(side="left")
        self.lbl(brand, APP, "brand").pack(anchor="w")
        self.sub = self.lbl(brand, tr("sub"), "brand", MUTE)
        self.sub.pack(anchor="w")
        self.counter = self.lbl(top, "", "mono", INK2)
        self.counter.pack(side="right", anchor="n")

        bot = tk.Frame(page, bg=BG)
        bot.pack(side="bottom", fill="x")
        tk.Frame(page, height=1, bg=RULE).pack(side="bottom", fill="x", pady=(0, 16))
        self.btn = Btn(bot, "", None, self.F["btn"])
        self.btn.pack(side="right")
        self.back = Btn(bot, "←", self.lang_step, self.F["btn"], kind="outline")  # вернуться к выбору языка
        self.bar = tk.Canvas(bot, width=260, height=10, bg=BG, highlightthickness=0)
        self.body = tk.Frame(page, bg=BG)
        self.body.pack(fill="both", expand=True, pady=(26, 0))
        r.bind("<Return>", lambda e: self.btn.enabled and self.btn.command())
        self.lang_step() if mode == "install" else self.main_step()
        r.mainloop()

    def lbl(self, parent, text, font="body", fg=INK, **kw):
        return tk.Label(parent, text=text, font=self.F[font], fg=fg, bg=BG, **kw)

    def head(self, text):
        self.lbl(self.body, text, "head", justify="left").pack(anchor="w", pady=(0, 20))

    def lang_step(self):
        """Первый экран — всегда по-английски: язык ещё не выбран."""
        for w in self.body.winfo_children():
            w.destroy()
        self.back.pack_forget()
        self.bar.pack_forget()
        self.counter.config(text="01 / 02")
        self.head("Choose\nlanguage.")
        row = tk.Frame(self.body, bg=BG)
        row.pack(anchor="w")
        segs = {}

        def choose(code):
            global LANG
            LANG = code
            for c, b in segs.items():
                b.selected = c == code
                b.draw()

        for code, name in (("en", "English"), ("ru", "Русский")):
            segs[code] = Btn(row, name, lambda c=code: choose(c), self.F["btn"], kind="seg")
            segs[code].pack(side="left", padx=(0, 6))
        choose(LANG)
        self.btn.set_text("Continue")
        self.btn.command = self.main_step

    def main_step(self):
        for w in self.body.winfo_children():
            w.destroy()
        install = self.mode == "install"
        self.counter.config(text="02 / 02" if install else "")
        self.sub.config(text=tr("sub"))
        self.head((tr("head_i") if install else tr("head_u")) + "\nSmooth Cursor.")
        self.status = self.lbl(self.body, tr("where") + str(TARGET) if install else "", "mono", MUTE,
                               anchor="w", justify="left", wraplength=600)
        self.status.pack(anchor="w")
        if install:
            row = tk.Frame(self.body, bg=BG)
            row.pack(anchor="w", pady=(14, 0))
            self.desktop = Toggle(row, True)
            self.desktop.pack(side="left")
            lab = self.lbl(row, tr("desktop"), cursor="hand2")
            lab.pack(side="left", padx=(10, 0))
            lab.bind("<Button-1>", lambda e: self.desktop.flip())
        self.bar.pack(side="left")
        if install:
            self.back.pack(side="right", padx=(0, 10))
        self.set_bar(0)
        self.btn.set_text(tr("install") if install else tr("remove"))
        self.btn.command = self.go

    def set_bar(self, pct):
        self.bar.delete("all")
        n = 20
        for i in range(n):
            on = i < round(n * pct / 100)
            self.bar.create_rectangle(i * 13, 0, i * 13 + 9, 9, fill=INK if on else BG, outline=INK if on else LINE)

    def go(self):
        if self.done:
            subprocess.Popen([str(TARGET / EXE)], cwd=str(TARGET))
            return self.root.destroy()
        if app_running():
            return self.status.config(text=tr("running"), fg=RED)
        self.btn.set_enabled(False)
        self.back.pack_forget()
        if self.mode == "uninstall":
            uninstall()
            self.set_bar(100)
            self.status.config(text=tr("removed"), fg=INK)
            return self.root.after(1500, self.root.destroy)
        self.status.config(text=tr("copying"), fg=INK)
        self.btn.set_text(tr("copying"))

        def work():
            try:
                install(self.desktop.value, lambda p: self.root.after(0, self.set_bar, p))
                self.root.after(0, self.finished)
            except Exception as e:
                self.root.after(0, self.failed, str(e))

        threading.Thread(target=work, daemon=True).start()

    def failed(self, err):
        self.back.pack(side="right", padx=(0, 10))
        self.status.config(text=err, fg=RED)
        self.btn.set_text(tr("install"))
        self.btn.set_enabled(True)

    def finished(self):
        self.done = True
        self.status.config(text=tr("done"), fg=INK)
        self.btn.set_text(tr("run"))
        self.btn.set_enabled(True)


if __name__ == "__main__":
    if "--uninstall" in sys.argv:
        LANG = read_cfg().get("ui", {}).get("lang", "en")
    Window("uninstall" if "--uninstall" in sys.argv else "install")
