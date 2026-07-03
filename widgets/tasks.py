"""
desky widget — Tasks
"""

import os
import time as _time
import requests
from PIL import Image, ImageDraw, ImageFont

W, H = 240, 320
PAD = 22

BG          = (10, 10, 10)
FG          = (242, 242, 242)
FG_MUTED    = (107, 107, 107)
LINE        = (34, 34, 34)
ACCENT_DONE = (91, 209, 122)

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")
BACKEND = "https://web-production-12607.up.railway.app"
CACHE_TTL = 15
_cache = {"data": None, "ts": 0.0}

_DUMMY = [
    {"title": "Solder SPI displays",    "done": True},
    {"title": "Deploy Go backend",      "done": True},
    {"title": "Wire Last.fm scrobbles", "done": False},
    {"title": "Design GIF widget",      "done": False},
    {"title": "3D-print enclosure",     "done": False},
]

def _fetch_tasks():
    now = _time.time()
    if (now - _cache["ts"]) < CACHE_TTL:
        return _cache["data"]
    _cache["ts"] = now
    try:
        resp = requests.get(BACKEND + "/widget/tasks", timeout=4)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            _cache["data"] = data
            return data
        elif isinstance(data, dict) and "items" in data:
            _cache["data"] = data["items"]
            return data["items"]
    except Exception as e: pass
    return _cache["data"]

def _font(name, size):
    try: return ImageFont.truetype(os.path.join(FONT_DIR, f"{name}.ttf"), size)
    except OSError: return ImageFont.load_default()

def _tw(d, text, font): return d.textlength(text, font=font)

def _truncate(d, text, font, max_w):
    if _tw(d, text, font) <= max_w: return text
    while text and _tw(d, text + "..", font) > max_w: text = text[:-1]
    return text + ".."

def render(items=None):
    if items is None: items = _fetch_tasks()
    if not items: items = _DUMMY

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    f_lbl   = _font("PressStart2P-Regular", 8)
    f_task  = _font("PressStart2P-Regular", 7)
    f_tiny  = _font("PressStart2P-Regular", 6)

    done = sum(1 for it in items if (it.get("done") is True or str(it.get("done")).lower() == "true"))
    n = len(items)

    d.text((PAD, PAD + 4), "TASKS", font=f_lbl, fill=FG_MUTED, anchor="la")
    d.text((W - PAD, PAD + 4), f"{done}/{n}", font=f_lbl, fill=ACCENT_DONE, anchor="ra")

    hy = PAD + 22
    d.line([PAD, hy, W - PAD, hy], fill=LINE, width=1)

    row_h = 34
    top = hy + 18

    for i, it in enumerate(items[:5]):
        cy = top + i * row_h + row_h // 2
        bx = PAD

        is_completed = (it.get("done") is True or str(it.get("done")).lower() == "true")

        if is_completed:
            d.rectangle([bx, cy - 7, bx + 14, cy + 7], fill=ACCENT_DONE)
            d.line([bx + 3, cy, bx + 6, cy + 3], fill=BG, width=2)
            d.line([bx + 6, cy + 3, bx + 11, cy - 4], fill=BG, width=2)
            color = (58, 58, 58)
        else:
            d.rectangle([bx, cy - 7, bx + 14, cy + 7], outline=(58, 58, 58), width=2)
            color = FG

        tx = bx + 24
        raw_text = it.get("title") or it.get("t") or it.get("text") or "UNKNOWN TASK"
        truncated_text = _truncate(d, str(raw_text).upper(), f_task, W - PAD - tx)
        d.text((tx, cy + 1), truncated_text, font=f_task, fill=color, anchor="lm")

        if is_completed:
            d.line([tx, cy + 1, tx + int(_tw(d, truncated_text, f_task)), cy + 1], fill=(58, 58, 58), width=1)

    fy = H - PAD - 12
    d.text((PAD, fy - 6), f"{done} OF {n} DONE", font=f_tiny, fill=FG_MUTED, anchor="la")
    d.rectangle([PAD, fy + 10, W - PAD, fy + 12], fill=LINE)

    frac = (done / n) if n else 0
    if frac > 0: d.rectangle([PAD, fy + 10, PAD + int((W - 2 * PAD) * frac), fy + 12], fill=ACCENT_DONE)
    return img
