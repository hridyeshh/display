"""
desky widget — Digital pet (Tamagotchi-style)
240x320 RGB. Name and state on the top row, a pixel hamster in the middle, and
a happiness bar over the last-fed line. Mirrors the `pet` state of DeskyPanel:
the bar accent steps green → amber → red as happiness drops.
"""

import time as _time
import requests
from PIL import Image, ImageDraw

from .theme import (
    W, H, PAD, BG, FG, FG_MUTED, ACCENT_WARM, ACCENT_MUSIC, ACCENT_DONE,
    label, bar,
)

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
PAL_BODY = {1: (230, 161, 90), 2: (245, 211, 168), 3: (245, 141, 160), 4: BG}
CELL = 12  # 12x11 grid → 144x132, the design's ~150x138 sprite box

BACKEND = "https://web-production-12607.up.railway.app"
CACHE_TTL = 30  # pet state changes slowly
_cache = {"data": None, "ts": 0.0}

SPRITE_CY = 147
STAT_Y = 259
BAR_Y = 275
FED_Y = 290


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


def _tint_gray(pal):
    out = {}
    for k, rgb in pal.items():
        m = sum(rgb) // 3
        out[k] = BG if k == 4 else (m, m, m)
    return out


def _accent(happiness):
    """One accent per screen, stepped — matches the design's petColor rule."""
    if happiness >= 50:
        return ACCENT_DONE
    return ACCENT_WARM if happiness >= 20 else ACCENT_MUSIC


def _creature(d, cx, cy, pal, eyes_closed, cell=CELL):
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
            d.line([x0, y0, x0 + cell, y0], fill=BG, width=3)


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
    happiness = max(0, min(100, happiness))
    state = state or "content"
    hours = int(hours_since_fed if hours_since_fed is not None else 0)
    accent = _accent(happiness)

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    f_lbl = label()

    d.text((PAD, PAD + 4), name, font=f_lbl, fill=FG_MUTED, anchor="la")
    d.text((W - PAD, PAD + 4), str(state).upper(), font=f_lbl, fill=accent, anchor="ra")

    cx, cy = W // 2, SPRITE_CY
    if state == "happy":
        # ponytail: two discrete poses, not an eased bounce — a single polled
        # repaint on the Pi has to land on a valid frame either way.
        _creature(d, cx, cy - (8 if int(_time.time()) % 2 == 0 else 0), PAL_BODY, False)
    elif state == "sad":
        _creature(d, cx, cy + 8, _tint_gray(PAL_BODY), False)
    elif state == "sleepy":
        _creature(d, cx, cy, _tint_gray(PAL_BODY), True)
        d.text((cx + 58, cy - 70), "ZZZ", font=f_lbl, fill=FG_MUTED, anchor="la")
    else:
        _creature(d, cx, cy, PAL_BODY, False)

    d.text((PAD, STAT_Y), "HAPPINESS", font=f_lbl, fill=FG_MUTED, anchor="la")
    d.text((W - PAD, STAT_Y), f"{happiness}%", font=f_lbl, fill=FG, anchor="ra")
    bar(d, PAD, BAR_Y, W - 2 * PAD, happiness / 100.0, accent)

    fed = "JUST NOW" if hours <= 0 else f"{hours}H AGO"
    d.text((PAD, FED_Y), f"LAST FED {fed}", font=f_lbl, fill=FG_MUTED, anchor="la")
    return img


if __name__ == "__main__":
    # ponytail: offline self-check — one PNG per state, no backend needed.
    assert _accent(80) == ACCENT_DONE and _accent(34) == ACCENT_WARM and _accent(5) == ACCENT_MUSIC
    for st, hp, hr in (("happy", 92, 0), ("content", 65, 8),
                       ("sad", 34, 18), ("sleepy", 12, 40)):
        render("Pixel", hp, st, hr).save(f"out_pet_{st}.png")
    print("wrote out_pet_<state>.png")
