"""
desky widget — GIF / pixel art player
"""

import os
import math
import hashlib
import urllib.request
import time as _time
from PIL import Image, ImageDraw, ImageFont

W, H = 240, 320
COLS, ROWS = 15, 20

BG           = (10, 10, 10)
FG           = (242, 242, 242)
ACCENT_WARM  = (232, 201, 122)
ACCENT_MUSIC = (252, 60, 68)

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")
MEDIA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "media")

_frames    = []
_durations = []
_mode      = "sunset"
_start_ts  = 0.0

def set_gif(path):
    global _frames, _durations, _mode, _start_ts
    frames, durations = load_gif_frames(path)
    if frames:
        _frames    = frames
        _durations = durations
        _mode      = "gif"
        _start_ts  = _time.monotonic()

_CACHE_DIR  = "/tmp/desky_gifs"
_url_loaded = None

def render_url(url):
    global _url_loaded
    if url and url != _url_loaded:
        try:
            os.makedirs(_CACHE_DIR, exist_ok=True)
            path = os.path.join(_CACHE_DIR, hashlib.md5(url.encode()).hexdigest() + ".gif")
            if not os.path.exists(path): urllib.request.urlretrieve(url, path)
            set_gif(path)
            _url_loaded = url
        except Exception: pass
    return render()

def set_pixel_art(path, cols=COLS, rows=ROWS):
    global _frames, _durations, _mode, _start_ts
    frames, durations = load_gif_frames(path, target_size=(cols, rows), pixel_art=True)
    if frames:
        _frames    = frames
        _durations = durations
        _mode      = "pixelart"
        _start_ts  = _time.monotonic()

def load_gif_frames(path, target_size=(W, H), pixel_art=False):
    frames, durations = [], []
    try:
        img = Image.open(path)
        mode = "RGB"
        resample = Image.NEAREST if pixel_art else Image.LANCZOS
        try:
            while True:
                frame = img.convert(mode).resize(target_size, resample)
                frames.append(frame)
                dur_ms = img.info.get("duration", 100)
                durations.append(max(dur_ms, 20) / 1000.0)
                img.seek(img.tell() + 1)
        except EOFError: pass
    except Exception: pass
    return frames, durations

def _current_frame_index():
    if not _frames: return 0
    elapsed = _time.monotonic() - _start_ts
    total = sum(_durations)
    if total <= 0: return 0
    t = elapsed % total
    acc = 0.0
    for i, d in enumerate(_durations):
        acc += d
        if t < acc: return i
    return len(_frames) - 1

_SUN   = ACCENT_WARM
_MOUNT = (21, 22, 31)
_LAND  = (12, 12, 12)
_GLOW  = (58, 36, 24)
_RIDGE = [14, 12, 11, 9, 10, 12, 10, 8, 9, 11, 12, 11, 10, 12, 14]
_STARS = {(2, 1), (12, 2), (5, 3), (10, 4), (1, 5), (13, 5)}
_SUN_CX, _SUN_CY, _SUN_R, _HORIZON = 7, 7, 2.7, 15

def _sky(r):
    if r <= 1:  return (11, 18, 38)
    if r <= 3:  return (22, 33, 63)
    if r <= 5:  return (42, 35, 80)
    if r <= 7:  return (90, 47, 92)
    if r <= 9:  return (154, 66, 87)
    if r <= 11: return (207, 106, 57)
    return (230, 154, 74)

def _cell(r, c):
    col = _sky(r)
    if r <= _HORIZON and math.hypot(c - _SUN_CX, r - _SUN_CY) <= _SUN_R: col = _SUN
    if _RIDGE[c] <= r <= _HORIZON: col = _MOUNT
    if r > _HORIZON: col = _LAND
    if r == 16 and abs(c - _SUN_CX) <= 2: col = _GLOW
    if r == 17 and abs(c - _SUN_CX) <= 1: col = _GLOW
    if (c, r) in _STARS and col == _sky(r): col = FG
    return col

def _font(name, size):
    try: return ImageFont.truetype(os.path.join(FONT_DIR, f"{name}.ttf"), size)
    except OSError: return ImageFont.load_default()

def _tw(d, text, font): return d.textlength(text, font=font)

def _tracked(d, xy, text, font, fill, tracking=0):
    x, y = xy
    for c in text:
        d.text((x, y), c, font=font, fill=fill)
        x += _tw(d, c, font) + tracking

def _overlay(img):
    d = ImageDraw.Draw(img)
    f = _font("PressStart2P-Regular", 8)
    d.rectangle([10, 10, 64, 32], fill=BG)
    d.rectangle([18, 17, 25, 24], fill=ACCENT_MUSIC)
    _tracked(d, (31, 14), "GIF", f, FG, tracking=3)
    d.rectangle([0, 0, W - 1, H - 1], outline=(28, 28, 28), width=1)
    return img

def render(grid=None):
    if grid is not None:
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        cw, ch = W / COLS, H / ROWS
        for r in range(ROWS):
            for c in range(COLS):
                d.rectangle([round(c * cw), round(r * ch), round((c + 1) * cw), round((r + 1) * ch)], fill=grid[r][c])
        return _overlay(img)

    if _mode == "gif" and _frames:
        img = _frames[_current_frame_index()].copy()
        return _overlay(img)

    if _mode == "pixelart" and _frames:
        small = _frames[_current_frame_index()]
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        cw, ch = W / small.width, H / small.height
        pix = small.load()
        for r in range(small.height):
            for c in range(small.width):
                d.rectangle([round(c * cw), round(r * ch), round((c + 1) * cw), round((r + 1) * ch)], fill=pix[c, r])
        return _overlay(img)

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    cw, ch = W / COLS, H / ROWS
    for r in range(ROWS):
        for c in range(COLS):
            d.rectangle([round(c * cw), round(r * ch), round((c + 1) * cw), round((r + 1) * ch)], fill=_cell(r, c))
    return _overlay(img)

def _auto_load():
    if not os.path.isdir(MEDIA_DIR): return
    for fname in sorted(os.listdir(MEDIA_DIR)):
        if fname.lower().endswith(".gif"):
            set_gif(os.path.join(MEDIA_DIR, fname))
            return
        if fname.lower().endswith((".png", ".bmp")):
            set_pixel_art(os.path.join(MEDIA_DIR, fname))
            return
_auto_load()
