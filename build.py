"""Сборка: python build.py → release/SmoothCursor-Setup.exe
1) иконка; 2) программа в папку (PyInstaller, без консоли); 3) папка в payload.zip; 4) установщик одним exe."""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
B = ROOT / "build"
PYI = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--log-level", "WARN", "--workpath", str(B / "work"),
       "--specpath", str(B)]


def run(cmd):
    print(">", " ".join(map(str, cmd[:6])), "…")
    subprocess.run(cmd, check=True, cwd=ROOT)


run([sys.executable, "make_icon.py"])
ico, png = ROOT / "assets" / "icon.ico", ROOT / "assets" / "icon.png"
run(PYI + ["--windowed", "--name", "Smooth Cursor", "--icon", str(ico), "--distpath", str(B / "dist"),
           "--add-data", f"{ROOT / 'fonts'};fonts", "--add-data", f"{ROOT / 'assets'};assets", str(ROOT / "app.pyw")])

app_dir = B / "dist" / "Smooth Cursor"
payload = B / "payload.zip"
with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for f in app_dir.rglob("*"):
        if f.is_file():
            z.write(f, f.relative_to(app_dir))
print("payload:", round(payload.stat().st_size / 2 ** 20, 1), "MB")

run(PYI + ["--onefile", "--windowed", "--name", "SmoothCursor-Setup", "--icon", str(ico), "--distpath",
           str(B / "setup"), "--add-data", f"{payload};.", "--add-data", f"{ico};.", "--add-data", f"{png};.",
           "--add-data", f"{ROOT / 'fonts'};fonts",
           str(ROOT / "installer.py")])
out = ROOT / "release"
out.mkdir(exist_ok=True)
shutil.copy2(B / "setup" / "SmoothCursor-Setup.exe", out / "SmoothCursor-Setup.exe")
print("готово:", out / "SmoothCursor-Setup.exe", round((out / "SmoothCursor-Setup.exe").stat().st_size / 2 ** 20, 1), "MB")
