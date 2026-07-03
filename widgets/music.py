"""
desky widget — Now Playing
240x320 RGB, authentic layout matching music.html.
Features clean, unpixelated high-resolution album art rendering.
"""

import os
import io
import math as _math
import time as _time
import threading
import urllib.parse
import requests
from PIL import Image, ImageDraw, ImageFont

W, H = 240, 320
PAD = 22
TOP_PAD = 38

BG           = (10, 10, 10)
BG2          = (17, 17, 17)
FG           = (242, 242, 242)
FG_MUTE      = (107, 107, 107)
FG_DIM       = (58, 58, 58)
LINE         = (34, 34, 34)
ACCENT_MUSIC = (252, 60, 68)

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")
BACKEND = "https://web-production-12607.up.railway.app"
POLL_INTERVAL = 5

_cache_lock = threading.Lock()
_cache      = {"data": None}
_art_cache  = {"url": "", "img": None}

# Last.fm serves this hash as its "no album art" placeholder star. Apple Music
# scrobbles almost never carry real art, so treat it as empty and fall back.
_LASTFM_PLACEHOLDER = "2a96cbd8b46e442fc41c2b86b821562f"
_itunes_cache = {"key": "", "url": None}

def _itunes_art(track, artist):
    """Look up real cover art from the iTunes Search API (Apple Music source)."""
    key = f"{track}|{artist}"
    if _itunes_cache["key"] == key:
        return _itunes_cache["url"]
    result = None
    try:
        term = urllib.parse.quote(f"{track} {artist}".strip())
        url = f"https://itunes.apple.com/search?term={term}&entity=song&limit=1"
        res = requests.get(url, timeout=5)
        results = res.json().get("results", [])
        if results:
            art = results[0].get("artworkUrl100", "")
            if art:
                result = art.replace("100x100bb", "300x300bb")
    except Exception:
        result = None
    _itunes_cache["key"] = key
    _itunes_cache["url"] = result
    return result

def _resolve_art(url, track, artist):
    """Prefer a real Last.fm URL; otherwise fall back to iTunes lookup."""
    if url and _LASTFM_PLACEHOLDER not in url:
        return url
    return _itunes_art(track, artist)

def _do_fetch():
    try:
        resp = requests.get(BACKEND + "/widget/spotify", timeout=4)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            with _cache_lock:
                _cache["data"] = data
    except Exception as e: pass

def _poll_loop():
    while True:
        _do_fetch()
        _time.sleep(POLL_INTERVAL)

def _fetch_spotify():
    with _cache_lock: return _cache["data"]

threading.Thread(target=_poll_loop, daemon=True, name="music-poller").start()

def _font(name, size):
    try: return ImageFont.truetype(os.path.join(FONT_DIR, f"{name}.ttf"), size)
    except OSError: return ImageFont.load_default()

def _tw(d, text, font): return d.textlength(text, font=font)

def _truncate(d, text, font, max_w):
    if _tw(d, text, font) <= max_w: return text
    while text and _tw(d, text + "..", font) > max_w: text = text[:-1]
    return text + ".."

def _mmss(s):
    s = int(max(0, s))
    return f"{s // 60}:{s % 60:02d}"

def _pixel_note(d, x, y, color):
    d.rectangle([x + 2,  y + 18, x + 10, y + 24], fill=color)
    d.rectangle([x + 14, y + 14, x + 22, y + 20], fill=color)
    d.rectangle([x + 8,  y + 4,  x + 10, y + 18], fill=color)
    d.rectangle([x + 20, y + 2,  x + 22, y + 14], fill=color)
    d.rectangle([x + 8,  y + 2,  x + 22, y + 6],  fill=color)

def _get_clean_album(url, size=46):
    global _art_cache
    if not url: return None
    if _art_cache["url"] == url and _art_cache["img"] is not None: return _art_cache["img"]

    try:
        res = requests.get(url, timeout=10)
        img_raw = Image.open(io.BytesIO(res.content))
        clean_art = img_raw.convert("RGB").resize((size, size), Image.Resampling.LANCZOS)
        _art_cache["url"] = url
        _art_cache["img"] = clean_art
        return clean_art
    except Exception as e: return None

def _draw_head(d, label, label_color, eq_color, img_canvas, art_url):
    d.rectangle([PAD, TOP_PAD, PAD + 46, TOP_PAD + 46], fill=BG2)

    art_img = _get_clean_album(art_url, size=46)
    if art_img: img_canvas.paste(art_img, (PAD, TOP_PAD))
    else: _pixel_note(d, PAD + 10, TOP_PAD + 10, FG_MUTE)

    lx = PAD + 46 + 14
    f_lbl = _font("PressStart2P-Regular", 8)
    d.text((lx, TOP_PAD + 10), label, font=f_lbl, fill=label_color, anchor="la")

    bar_y = TOP_PAD + 24
    if label_color == ACCENT_MUSIC:
        t = _time.time() * 12
        hgts = [
            int(9 + 5 * _math.sin(t)),
            int(9 + 5 * _math.sin(t + 1.5)),
            int(9 + 5 * _math.sin(t + 3.0)),
            int(9 + 5 * _math.sin(t + 4.5))
        ]
        hgts = [max(3, min(14, h)) for h in hgts]
    else:
        hgts = [4, 4, 4, 4]

    for i, hgt in enumerate(hgts):
        bx = lx + i * 6
        d.rectangle([bx, bar_y + (14 - hgt), bx + 3, bar_y + 14], fill=eq_color)

def _empty_progress(d):
    by = H - 56
    d.rectangle([PAD, by - 1, W - PAD, by + 1], fill=LINE)
    f_lbl = _font("PressStart2P-Regular", 8)
    d.text((PAD, by + 14), "0:00", font=f_lbl, fill=FG_DIM, anchor="la")
    d.text((W - PAD, by + 14), "0:00", font=f_lbl, fill=FG_DIM, anchor="ra")

def _render_not_configured(img, d):
    _draw_head(d, "NOW PLAYING", FG_MUTE, FG_DIM, img, None)
    f_lbl = _font("PressStart2P-Regular", 8)
    d.text((PAD, 150), "NOT CONFIGURED", font=f_lbl, fill=FG_MUTE, anchor="la")
    d.text((PAD, 174), "Check API keys", font=f_lbl, fill=FG_DIM, anchor="la")
    _empty_progress(d)
    return img

def _render_idle(img, d):
    _draw_head(d, "NOW PLAYING", FG_MUTE, FG_DIM, img, None)
    f_lbl = _font("PressStart2P-Regular", 8)
    d.text((PAD, 150), "SILENT", font=f_lbl, fill=FG_MUTE, anchor="la")
    d.text((PAD, 174), "Play some music...", font=f_lbl, fill=FG_DIM, anchor="la")
    _empty_progress(d)
    return img

def render(track=None, artist=None, album=None, elapsed=None, duration=None):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    art_url = None

    if track is None and artist is None:
        data = _fetch_spotify()
        if not data:
            data = {"status": "not_configured"}
        status = data.get("status", "not_configured")

        if status == "not_configured": return _render_not_configured(img, d)
        if status != "playing": return _render_idle(img, d)

        track    = data.get("track", "")
        artist   = data.get("artist", "")
        album    = data.get("album", "")
        art_url  = data.get("image_url", "")
        elapsed  = data.get("progress_ms", 0) / 1000.0
        duration = data.get("duration_ms", 0) / 1000.0

    track = (track or "Midnight City")
    artist = (artist or "M83")
    album = (album or "Hurry Up, We're Dreaming")
    elapsed = elapsed if elapsed is not None else 83
    duration = duration if duration is not None else 244

    f_lbl   = _font("VT323-Regular", 34)
    f_sub   = _font("VT323-Regular", 20)
    f_tiny  = _font("VT323-Regular", 16)

    f_time  = _font("PressStart2P-Regular", 8)

    art_url = _resolve_art(art_url, track, artist)
    _draw_head(d, "NOW PLAYING", ACCENT_MUSIC, ACCENT_MUSIC, img, art_url)

    inner = W - 2 * PAD

    d.text((PAD, 142), _truncate(d, str(track).upper(), f_lbl, inner), font=f_lbl, fill=FG, anchor="la")
    d.text((PAD, 172), _truncate(d, str(artist).upper(), f_sub, inner), font=f_sub, fill=FG_MUTE, anchor="la")
    d.text((PAD, 192), _truncate(d, str(album).upper(), f_tiny, inner), font=f_tiny, fill=FG_DIM, anchor="la")

    by = H - 56
    d.rectangle([PAD, by - 1, W - PAD, by + 1], fill=LINE)
    if duration > 0:
        frac = max(0.0, min(1.0, elapsed / duration))
        kx = PAD + int((W - 2 * PAD) * frac)
        d.rectangle([PAD, by - 1, kx, by + 1], fill=ACCENT_MUSIC)
        d.rectangle([kx - 3, by - 3, kx + 3, by + 3], fill=ACCENT_MUSIC)

        d.text((PAD, by + 14), _mmss(elapsed), font=f_time, fill=FG_MUTE, anchor="la")
        d.text((W - PAD, by + 14), _mmss(duration), font=f_time, fill=FG_MUTE, anchor="ra")
    return img
