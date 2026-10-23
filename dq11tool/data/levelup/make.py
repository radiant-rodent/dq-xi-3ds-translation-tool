"""Renders the English "LEVEL UP!" letter sprites (32x32) in the style of the game's
result_lv*.bflim (yellow-to-red gradient, dark outline, soft shadow). Run once on Windows;
the PNGs next to this file are what the exporter uses.

    python dq11tool/data/levelup/make.py
"""
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT = r"C:\Windows\Fonts\seguibl.ttf"          # Segoe UI Black
S = 4                                            # supersampling
TOP, BOTTOM = 7, 24                              # cap height rows (the Japanese letters span 8..23)
# fill colour per row (sampled from the original sprites, rows 8..22)
GRADIENT = [(240, 177, 6), (255, 189, 23), (255, 176, 10), (245, 150, 6), (240, 139, 6), (244, 132, 8),
            (244, 120, 8), (238, 107, 3), (233, 97, 4), (242, 89, 0), (238, 72, 0), (246, 63, 0),
            (255, 51, 0), (246, 42, 0), (250, 40, 0), (250, 38, 0)]
OUTLINE = (34, 0, 0)
SHEAR = 0.18                                     # slant like the original lettering


def letter(ch: str) -> Image.Image:
    font = ImageFont.truetype(FONT, 100)
    l, t, r, b = font.getbbox(ch)
    m = Image.new("L", (r - l + 40, b - t + 40), 0)
    ImageDraw.Draw(m).text((20 - l, 20 - t), ch, font=font, fill=255)
    m = m.transform(m.size, Image.AFFINE, (1, SHEAR, -SHEAR * m.size[1] / 2, 0, 1, 0), Image.BICUBIC)
    m = m.crop(m.getbbox())
    h = (BOTTOM - TOP) * S
    w = max(1, round(m.size[0] * h / m.size[1]))
    m = m.resize((w, h), Image.LANCZOS)
    big = Image.new("L", (32 * S, 32 * S), 0)
    big.paste(m, ((32 * S - w) // 2, TOP * S))
    fill = np.array(big.resize((32, 32), Image.LANCZOS), dtype=np.float32) / 255
    outline = np.array(big.filter(ImageFilter.MaxFilter(2 * S + 1)).resize((32, 32), Image.LANCZOS),
                       dtype=np.float32) / 255
    shadow = np.array(big.filter(ImageFilter.MaxFilter(4 * S + 1)).filter(ImageFilter.GaussianBlur(S))
                      .resize((32, 32), Image.LANCZOS), dtype=np.float32) / 255 * 0.25
    rgba = np.zeros((32, 32, 4), np.float32)
    for y in range(32):
        g = GRADIENT[min(max(y - 8, 0), len(GRADIENT) - 1)]
        for x in range(32):
            a_f, a_o, a_s = fill[y, x], outline[y, x], shadow[y, x]
            col = np.array(OUTLINE, np.float32) * (1 - a_f) + np.array(g, np.float32) * a_f
            a = max(a_o, a_f)
            if a < a_s:
                col, a = np.zeros(3), a_s
            rgba[y, x, :3] = col
            rgba[y, x, 3] = a * 255
    return Image.fromarray(rgba.clip(0, 255).astype(np.uint8), "RGBA")


if __name__ == "__main__":
    for ch, name in (("L", "L"), ("E", "E"), ("V", "V"), ("U", "U"), ("P", "P"), ("!", "excl")):
        letter(ch).save(os.path.join(HERE, name + ".png"))
