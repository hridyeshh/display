"""
desky widget — Calendar event banner
"""

import os
import textwrap
from PIL import Image, ImageDraw, ImageFont

W, H = 240, 320

BG    = (10, 10, 10)
FG    = (242, 242, 242)
AMBER = (232, 201, 122)
MUTED = (107, 107, 107)
LINE  = (34, 34, 34)

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")

def _font(name, size):
    try: return ImageFont.truetype(os.path.join(FONT_DIR, f"{name}.ttf"), size)
    except OSError: return ImageFont.load_default()

def render_banner(title, when_str, mins_until) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    cx = W // 2

    d.rectangle([0, 0, W, 6], fill=AMBER)

    d.text((cx, 54), "UPCOMING", font=_font("PressStart2P-Regular", 12), fill=AMBER, anchor="mm")
    d.text((cx, 78), "EVENT", font=_font("PressStart2P-Regular", 8), fill=MUTED, anchor="mm")

    title = (title or "Event").strip()
    f_title = _font("VT323-Regular", 34)
    lines = textwrap.wrap(title, width=14)[:2]
    y = 138
    for ln in lines:
        d.text((cx, y), ln, font=f_title, fill=FG, anchor="mm")
        y += 28

    d.line([40, 210, W - 40, 210], fill=LINE, width=1)

    d.text((cx, 238), f"IN {int(mins_until)} MIN", font=_font("PressStart2P-Regular", 10), fill=AMBER, anchor="mm")
    if when_str:
        d.text((cx, 266), when_str, font=_font("PressStart2P-Regular", 8), fill=MUTED, anchor="mm")
    return img
