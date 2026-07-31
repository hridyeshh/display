"""
desky widget — Countdown Timer
240x320 RGB. Green TIMER label, VT323 hero MM:SS, the wall-clock time it ends,
and a bar that depletes with the remaining time. Mirrors the `timer` state of
DeskyPanel.
"""

import time as _time
from PIL import Image, ImageDraw

from .theme import (
    W, H, PAD, BG, FG, FG_MUTED, LINE, ACCENT_DONE, ACCENT_ERROR,
    hero, label, bar,
)

# Byte's error red at a third brightness reads as "off" in the done-state flash.
ERR_DARK = (142, 35, 41)


def _fmt(secs):
    secs = max(0, int(secs))
    return f"{secs // 60:02d}:{secs % 60:02d}"


def render(remaining_sec, total_sec=None) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    f_lbl = label()

    d.text((PAD, PAD + 4), "TIMER", font=f_lbl, fill=ACCENT_DONE, anchor="la")

    cy = 150
    d.text((W // 2, cy), _fmt(remaining_sec), font=hero(72), fill=FG, anchor="mm")

    ends = _time.strftime("%H:%M", _time.localtime(_time.time() + max(0, remaining_sec)))
    d.text((W // 2, cy + 50), f"ENDS {ends}", font=f_lbl, fill=FG_MUTED, anchor="mm")

    frac = (max(0, remaining_sec) / total_sec) if total_sec and total_sec > 0 else 0.0
    bar(d, PAD, H - PAD - 3, W - 2 * PAD, frac, ACCENT_DONE)
    return img


def render_done(flash_on=True) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    color = ACCENT_ERROR if flash_on else ERR_DARK

    d.text((PAD, PAD + 4), "TIMER", font=label(), fill=color, anchor="la")

    cy = 150
    d.text((W // 2, cy), "00:00", font=hero(72), fill=LINE, anchor="mm")
    d.text((W // 2, cy + 50), "TIME'S UP", font=label(10), fill=color, anchor="mm")

    d.rectangle([PAD, H - PAD - 3, W - PAD, H - PAD - 1], fill=color)
    return img


if __name__ == "__main__":
    # ponytail: the bar fraction is the only branch worth pinning down.
    render(1500, 1500).save("out_timer_full.png")
    render(90, 1500).save("out_timer_low.png")
    render(0, None).save("out_timer_zero.png")
    render_done(True).save("out_timer_done.png")
    print("wrote out_timer_*.png")
