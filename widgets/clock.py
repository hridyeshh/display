"""
desky widget — Clock
240x320 RGB. IST label, VT323 hero time with an amber colon, day + date,
and a seconds bar along the bottom. Mirrors the `clock` state of DeskyPanel.
"""

import datetime
from PIL import Image, ImageDraw

from .theme import (
    W, H, PAD, BG, FG, FG_MUTED, ACCENT_WARM,
    hero, label, tw, bar,
)

_DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
_MON  = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _seg_row(d, cx, cy, segments, gap=2):
    widths = [tw(d, t, f) for t, f, _ in segments]
    total = sum(widths) + gap * (len(segments) - 1)
    x = cx - total / 2
    for (t, f, fill), w in zip(segments, widths):
        d.text((x, cy), t, font=f, fill=fill, anchor="lm")
        x += w + gap


def render(time=None, date=None, day=None, secs=None):
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

    f_time = hero(78)
    f_lbl  = label()

    d.text((PAD, PAD + 4), "IST", font=f_lbl, fill=FG_MUTED, anchor="la")

    if ":" in str(time):
        hh, mm = str(time).split(":")[:2]
    else:
        hh, mm = now.strftime("%H"), now.strftime("%M")

    cy = 150
    _seg_row(d, W // 2, cy, [
        (hh,  f_time, FG),
        (":", f_time, ACCENT_WARM),
        (mm,  f_time, FG),
    ], gap=2)

    d.text((W // 2, cy + 48), str(day).upper(), font=f_lbl, fill=FG, anchor="mm")
    d.text((W // 2, cy + 68), str(date).upper(), font=f_lbl, fill=ACCENT_WARM, anchor="mm")

    bar(d, PAD, H - PAD - 3, W - 2 * PAD, secs_i / 60.0, ACCENT_WARM)
    return img
