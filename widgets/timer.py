"""
desky widget — Countdown Timer
"""

import os
from PIL import Image, ImageDraw, ImageFont

W, H = 240, 320
PAD  = 22

BG           = (10, 10, 10)
FG           = (242, 242, 242)
FG_MUTED     = (107, 107, 107)
LINE         = (34, 34, 34)
ACCENT_GREEN = (122, 232, 153)
RED          = (255, 59, 48)
RED_DIM      = (90, 24, 20)

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")

def _font(name, size):
    try: return ImageFont.truetype(os.path.join(FONT_DIR, f"{name}.ttf"), size)
    except OSError: return ImageFont.load_default()

def _tw(d, text, font): return d.textlength(text, font=font)

def _time_row(d, cx, cy, mm, ss, accent):
    f = _font("VT323-Regular", 78)
    parts = [(mm, FG), (":", accent), (ss, FG)]
    widths = [_tw(d, t, f) for t, _ in parts]
    total  = sum(widths) + 2 * 2
    x = cx - total / 2
    for (t, fill), w in zip(parts, widths):
        d.text((x, cy), t, font=f, fill=fill, anchor="lm")
        x += w + 2

def _fmt(secs):
    secs = max(0, int(secs))
    return f"{secs // 60:02d}", f"{secs % 60:02d}"

def render(remaining_sec, total_sec=None) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d   = ImageDraw.Draw(img)
    cx  = W // 2

    f_lbl = _font("PressStart2P-Regular", 8)

    d.text((PAD, PAD + 4),      "TIMER",     font=f_lbl, fill=FG_MUTED,     anchor="la")
    d.text((PAD, PAD + 18),     "COUNTDOWN", font=f_lbl, fill=FG_MUTED,     anchor="la")
    d.text((W - PAD, PAD + 6),  "CDN",       font=f_lbl, fill=ACCENT_GREEN, anchor="ra")

    cy = 150
    mm, ss = _fmt(remaining_sec)
    _time_row(d, cx, cy, mm, ss, ACCENT_GREEN)

    d.text((cx, cy + 50), "REMAINING", font=f_lbl, fill=FG_MUTED, anchor="mm")

    by = H - PAD
    d.rectangle([PAD, by - 1, W - PAD, by + 1], fill=LINE)
    if total_sec and total_sec > 0:
        elapsed = max(0, total_sec - remaining_sec)
        frac    = min(1.0, elapsed / total_sec)
        if frac > 0:
            d.rectangle([PAD, by - 1, PAD + int((W - 2 * PAD) * frac), by + 1], fill=ACCENT_GREEN)

    return img

def render_done(flash_on=True) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d   = ImageDraw.Draw(img)
    cx  = W // 2
    f_lbl = _font("PressStart2P-Regular", 8)

    d.text((PAD, PAD + 4), "TIMER", font=f_lbl, fill=FG_MUTED, anchor="la")

    cy = 150
    _time_row(d, cx, cy, "00", "00", LINE)

    color = RED if flash_on else RED_DIM
    d.text((cx, cy + 50), "TIME'S UP", font=_font("PressStart2P-Regular", 10), fill=color, anchor="mm")

    by = H - PAD
    d.rectangle([PAD, by - 1, W - PAD, by + 1], fill=RED if flash_on else RED_DIM)
    return img
