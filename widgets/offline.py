"""
desky widget — Offline / No Internet
"""

import os
from PIL import Image, ImageDraw, ImageFont

W, H = 240, 320

BG    = (10, 10, 10)
RED   = (255, 59, 48)
MUTED = (107, 107, 107)
DIM   = (50, 50, 50)

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")

def _font(name, size):
    try: return ImageFont.truetype(os.path.join(FONT_DIR, f"{name}.ttf"), size)
    except OSError: return ImageFont.load_default()

def render() -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    cx = W // 2
    origin_y = 142

    for radius, width in [(58, 5), (40, 4), (22, 4)]:
        bbox = [cx - radius, origin_y - radius, cx + radius, origin_y + radius]
        d.arc(bbox, start=210, end=330, fill=RED, width=width)

    dot_y = origin_y + 14
    dot_r = 6
    d.ellipse([cx - dot_r, dot_y - dot_r, cx + dot_r, dot_y + dot_r], fill=RED)

    pad = 6
    d.line([cx + 58 + pad, origin_y - 58 - pad, cx - 58 - pad, dot_y + dot_r + pad], fill=RED, width=4)

    sep_y = 215
    d.line([32, sep_y, W - 32, sep_y], fill=DIM, width=1)

    f_main = _font("PressStart2P-Regular", 12)
    d.text((cx, sep_y + 26), "NO INTERNET", font=f_main, fill=RED, anchor="mm")

    f_sub = _font("PressStart2P-Regular", 8)
    d.text((cx, sep_y + 54), "Check WiFi connection", font=f_sub, fill=MUTED, anchor="mm")

    return img
