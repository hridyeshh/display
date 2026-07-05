"""
desky widget — Digital pet (Tamagotchi-style)
240x320 RGB. A pixel creature whose pose/colour reflect its happiness state,
fed from the backend. Bouncy when happy, droopy when sad, asleep when neglected.
"""

import os
import time as _time
import requests
from PIL import Image, ImageDraw, ImageFont

W, H = 240, 320
PAD = 22

BG       = (10, 10, 10)
FG       = (242, 242, 242)
FG_MUTED = (107, 107, 107)
LINE     = (34, 34, 34)
RED      = (252, 60, 68)
GREEN    = (91, 209, 122)

# Front-facing pixel hamster (shared with the iOS PetSprite + design mockup).
# Palette: 1 body, 2 belly, 3 cheek, 4 dark.
HAMSTER = [
    [0, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 0],
    [0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0],
    [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0],
    [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
    [1, 1, 4, 1, 1, 1, 1, 1, 1, 4, 1, 1],
    [1, 3, 3, 1, 1, 2, 2, 1, 1, 3, 3, 1],
    [1, 3, 3, 1, 2, 2, 2, 2, 1, 3, 3, 1],
    [1, 1, 1, 1, 2, 4, 4, 2, 1, 1, 1, 1],
    [0, 1, 1, 1, 2, 2, 2, 2, 1, 1, 1, 0],
    [0, 0, 1, 1, 1, 2, 2, 1, 1, 1, 0, 0],
    [0, 0, 0, 1, 1, 1, 1, 1, 1, 0, 0, 0],
]
PAL_BODY = {1: (230, 161, 90), 2: (245, 211, 168), 3: (245, 141, 160), 4: (10, 10, 10)}


def _tint_gray(pal):
    out = {}
    for k, rgb in pal.items():
        m = sum(rgb) // 3
        out[k] = (10, 10, 10) if k == 4 else (m, m, m)
    return out

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")
BACKEND = "https://web-production-12607.up.railway.app"
CACHE_TTL = 30  # 30s — pet state changes slowly
_cache = {"data": None, "ts": 0.0}


def _fetch_pet():
    now = _time.time()
    if _cache["data"] is not None and (now - _cache["ts"]) < CACHE_TTL:
        return _cache["data"]
    try:
        resp = requests.get(BACKEND + "/widget/pet", timeout=4)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "happiness" in data:
            _cache["data"] = data
            _cache["ts"] = now
            return data
    except Exception:
        pass
    return _cache["data"]


def _font(name, size):
    try:
        return ImageFont.truetype(os.path.join(FONT_DIR, f"{name}.ttf"), size)
    except OSError:
        return ImageFont.load_default()


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _creature(d, cx, cy, pal, eyes_closed, cell=10):
    """Draw the pixel hamster centred on (cx, cy) from the shared grid."""
    cols, rows = len(HAMSTER[0]), len(HAMSTER)
    ox = cx - cols * cell // 2
    oy = cy - rows * cell // 2
    for r, row in enumerate(HAMSTER):
        for c, v in enumerate(row):
            if not v:
                continue
            color = pal.get(v)
            if color:
                x0, y0 = ox + c * cell, oy + r * cell
                d.rectangle([x0, y0, x0 + cell - 1, y0 + cell - 1], fill=color)
    if eyes_closed:
        # Overdraw the two eye pixels (row 4, cols 2 & 9) as closed lines.
        for ec in (2, 9):
            x0 = ox + ec * cell
            y0 = oy + 4 * cell + cell // 2
            d.line([x0, y0, x0 + cell, y0], fill=(10, 10, 10), width=3)


def _sparkles(d, cx, cy):
    for sx, sy in ((cx - 60, cy - 40), (cx + 66, cy - 70), (cx + 74, cy - 10), (cx - 70, cy + 20)):
        d.rectangle([sx, sy, sx + 3, sy + 3], fill=(255, 240, 180))
        d.point([(sx - 4, sy + 1), (sx + 7, sy + 1), (sx + 1, sy - 4), (sx + 1, sy + 7)],
                fill=(255, 240, 180))


def render(name=None, happiness=None, state=None, hours_since_fed=None):
    if happiness is None and state is None:
        data = _fetch_pet()
        if data:
            name = data.get("name")
            happiness = data.get("happiness")
            state = data.get("state")
            hours_since_fed = data.get("hours_since_fed")

    name = (name or "Pixel").upper()
    happiness = int(happiness if happiness is not None else 80)
    state = state or "content"
    hours = int(hours_since_fed if hours_since_fed is not None else 0)

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # Name at top.
    d.text((W // 2, PAD), name, font=_font("PressStart2P-Regular", 10),
           fill=FG, anchor="ma")

    cx, cy = W // 2, 150

    if state == "happy":
        cy -= 8 if int(_time.time()) % 2 == 0 else 0  # bounce between two poses
        _sparkles(d, cx, cy)
        _creature(d, cx, cy, PAL_BODY, False)
    elif state == "sad":
        _creature(d, cx, cy + 8, _tint_gray(PAL_BODY), False)   # drooping, greyed
    elif state == "sleepy":
        _creature(d, cx, cy, _tint_gray(PAL_BODY), True)
        d.text((cx + 62, cy - 78), "Zzz", font=_font("SpaceGrotesk-Medium", 20),
               fill=FG_MUTED, anchor="la")
    else:
        _creature(d, cx, cy, PAL_BODY, False)            # content idle

    # Happiness bar (green→red by percentage).
    bar_x, bar_y, bar_w, bar_h = PAD, H - 70, W - 2 * PAD, 12
    d.rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], fill=LINE)
    fill_w = int(bar_w * max(0, min(100, happiness)) / 100)
    bar_col = _lerp(RED, GREEN, happiness / 100.0)
    if fill_w > 0:
        d.rectangle([bar_x, bar_y, bar_x + fill_w, bar_y + bar_h], fill=bar_col)
    d.text((bar_x, bar_y - 16), "HAPPINESS", font=_font("PressStart2P-Regular", 7),
           fill=FG_MUTED, anchor="la")
    d.text((bar_x + bar_w, bar_y - 16), f"{happiness}%",
           font=_font("PressStart2P-Regular", 7), fill=FG, anchor="ra")

    # Last-fed line.
    fed = "just now" if hours <= 0 else f"{hours}h ago"
    d.text((W // 2, H - 30), f"LAST FED: {fed.upper()}",
           font=_font("PressStart2P-Regular", 6), fill=FG_MUTED, anchor="ma")

    return img


if __name__ == "__main__":
    # ponytail: offline self-check — one PNG per state, no backend needed.
    for st, hp, hr in (("happy", 92, 0), ("content", 65, 8),
                       ("sad", 34, 18), ("sleepy", 12, 40)):
        render("Pixel", hp, st, hr).save(f"out_pet_{st}.png")
    print("wrote out_pet_<state>.png")
