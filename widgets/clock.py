"""
desky widget — Clock
240x320 RGB, pixelated VT323 clock + PressStart2P metadata labels.
"""

import os
import datetime
from PIL import Image, ImageDraw, ImageFont

W, H = 240, 320
PAD = 22

BG          = (10, 10, 10)
BORDER_FG   = (242, 242, 242)
FG_MUTED    = (107, 107, 107)
LINE        = (34, 34, 34)
ACCENT_WARM = (232, 201, 122)

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")

def _font(name, size):
    try:
        return ImageFont.truetype(os.path.join(FONT_DIR, f"{name}.ttf"), size)
    except OSError:
        return ImageFont.load_default()

def _tw(d, text, font):
    return d.textlength(text, font=font)

def _seg_row(d, cx, cy, segments, gap=2):
    widths = [_tw(d, t, f) for t, f, _ in segments]
    total = sum(widths) + gap * (len(segments) - 1)
    x = cx - total / 2
    for (t, f, fill), w in zip(segments, widths):
        d.text((x, cy), t, font=f, fill=fill, anchor="lm")
        x += w + gap

_DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
_MON  = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

def render(time=None, date=None, day=None, secs=None, zone="BENGALURU"):
    now = datetime.datetime.now()
    if time is None:
        time = now.strftime("%H:%M")
    if date is None:
        date = f"{now.day:02d} {_MON[now.month - 1]}"
    if day is None:
        day = _DAYS[now.weekday()]
    if secs is None:
        secs = now.second

    try:
        secs_i = int(float(secs))
    except (ValueError, TypeError):
        secs_i = 0

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    f_time  = _font("VT323-Regular", 78)
    f_lbl   = _font("PressStart2P-Regular", 8)
    f_sec   = _font("PressStart2P-Regular", 10)

    d.text((PAD, PAD + 4), "IST", font=f_lbl, fill=FG_MUTED, anchor="la")
    d.text((PAD, PAD + 18), str(zone).upper(), font=f_lbl, fill=FG_MUTED, anchor="la")
    d.text((W - PAD, PAD + 6), f"{secs_i:02d}", font=f_sec, fill=ACCENT_WARM, anchor="ra")

    if ":" in str(time):
        hh, mm = str(time).split(":")[:2]
    else:
        hh, mm = now.strftime("%H"), now.strftime("%M")

    cy = 150
    _seg_row(d, W // 2, cy, [
        (hh,  f_time, BORDER_FG),
        (":", f_time, ACCENT_WARM),
        (mm,  f_time, BORDER_FG),
    ], gap=2)

    d.text((W // 2, cy + 48), str(day).upper(), font=f_lbl, fill=BORDER_FG, anchor="mm")
    d.text((W // 2, cy + 68), str(date).upper(), font=f_lbl, fill=ACCENT_WARM, anchor="mm")

    by = H - PAD
    d.rectangle([PAD, by - 1, W - PAD, by + 1], fill=LINE)
    frac = secs_i / 60.0
    if frac > 0:
        d.rectangle([PAD, by - 1, PAD + int((W - 2 * PAD) * frac), by + 1], fill=ACCENT_WARM)
    return img
