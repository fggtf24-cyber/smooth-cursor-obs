"""Иконка приложения: пиксельная стрелка курсора на чёрной плитке с синим пиксельным свечением.
python make_icon.py → assets/icon.ico (+ icon.png для превью)."""
import math
from pathlib import Path

from PIL import Image, ImageDraw

ARROW = ["X......", "XX.....", "XXX....", "XXXX...", "XXXXX..", "XXXXXX.", "XXXXXXX", "XXXX...", "XX.XX..",
         "X..XX..", "....XX.", "....XX."]


def icon(size=256):
    n, k = 16, size // 16  # сетка 16×16 «пикселей»
    im = Image.new("RGBA", (size, size), (28, 28, 28, 255))
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    tx, ty = 5, 2  # кончик стрелки
    for i in range(n):  # свечение: синие клетки, тем прозрачнее, чем дальше от кончика
        for j in range(n):
            a = max(0.0, 1 - math.hypot(i - tx - 2.5, j - ty - 4.5) / 5.6)
            if a:
                g.rectangle([i * k, j * k, (i + 1) * k - 1, (j + 1) * k - 1], fill=(38, 50, 232, int(255 * a ** 1.2)))
    im.alpha_composite(glow)
    d = ImageDraw.Draw(im)
    for y, row in enumerate(ARROW):
        for x, ch in enumerate(row):
            if ch == "X":
                d.rectangle([(tx + x) * k, (ty + y) * k, (tx + x + 1) * k - 1, (ty + y + 1) * k - 1],
                            fill=(236, 236, 236, 255))
    return im


if __name__ == "__main__":
    out = Path(__file__).with_name("assets")
    out.mkdir(exist_ok=True)
    big = icon(256)
    big.save(out / "icon.png")
    big.save(out / "icon.ico", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print(out / "icon.ico")
