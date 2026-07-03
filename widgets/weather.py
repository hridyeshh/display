"""
desky widget — Weather
240x320 RGB, crisp VT323 temperature + pixelated weather icon grids.
"""

import os
import time as _time
import requests
from PIL import Image, ImageDraw, ImageFont

W, H = 240, 320
PAD = 22

BG          = (10, 10, 10)
BORDER_FG   = (242, 242, 242)
FG_MUTED    = (107, 107, 107)
LINE        = (34, 34, 34)
ACCENT_WARM = (232, 201, 122)
ACCENT_COOL = (122, 200, 232)

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")
BACKEND = "https://web-production-12607.up.railway.app"
CACHE_TTL = 60
_cache = {"data": None, "ts": 0.0}

def _fetch_weather():
    now = _time.time()
    if (now - _cache["ts"]) < CACHE_TTL:
        return _cache["data"]
    _cache["ts"] = now
    try:
        resp = requests.get(BACKEND + "/widget/weather", timeout=4)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "error" not in data and "temp" in data:
            _cache["data"] = data
            return data
    except Exception as e: pass
    return _cache["data"]

def _font(name, size):
    try: return ImageFont.truetype(os.path.join(FONT_DIR, f"{name}.ttf"), size)
    except OSError: return ImageFont.load_default()

def _tw(d, text, font): return d.textlength(text, font=font)

def _temp_accent(temp_c):
    if temp_c >= 26: return ACCENT_WARM
    if temp_c <= 14: return ACCENT_COOL
    return BORDER_FG

def _pixel_cloud(d, ox, oy, fill):
    d.rectangle([ox + 12, oy + 10, ox + 42, oy + 32], fill=fill)
    d.rectangle([ox + 4,  oy + 16, ox + 50, oy + 32], fill=fill)
    d.rectangle([ox + 2,  oy + 22, ox + 54, oy + 32], fill=fill)

def _icon(d, x, y, icon, accent):
    cx, cy = x + 37, y + 37
    if icon == "sun":
        d.rectangle([cx - 12, cy - 12, cx + 12, cy + 12], outline=accent, width=3)
        for offset in [-22, -18, 18, 22]:
            d.rectangle([cx - 2, cy + offset - 2, cx + 2, cy + offset + 2], fill=accent)
            d.rectangle([cx + offset - 2, cy - 2, cx + offset + 2, cy + 2], fill=accent)
    elif icon == "partly":
        d.rectangle([x + 36, y + 10, x + 56, y + 30], fill=accent)
        _pixel_cloud(d, x + 4, y + 18, BORDER_FG)
    elif icon == "rain":
        _pixel_cloud(d, x + 6, y + 10, BORDER_FG)
        for dx in (20, 34, 48):
            d.rectangle([x + dx, y + 46, x + dx + 3, y + 56], fill=ACCENT_COOL)
    else:
        _pixel_cloud(d, x + 8, y + 14, BORDER_FG)

_ICON_MAP = {
    "clear": "sun", "sun": "sun", "sunny": "sun",
    "cloud": "cloud", "cloudy": "cloud", "overcast": "cloud",
    "partly": "partly", "partly cloudy": "partly", "few clouds": "partly",
    "rain": "rain", "drizzle": "rain", "shower": "rain"
}

def render(city=None, temp=None, cond=None, humidity=None, feels=None, icon=None):
    if temp is None and cond is None and icon is None:
        data = _fetch_weather()
        if data:
            temp = data.get("temp")
            cond = data.get("condition")
            humidity = data.get("humidity")
            icon = _ICON_MAP.get(str(data.get("icon") or data.get("condition")).strip().lower(), "cloud")
            feels = data.get("feels_like") or data.get("feels")

    city = (city or "BANGALORE")
    cond = str(cond or "PARTLY CLOUDY").upper()

    humidity_str = f"{humidity}%" if isinstance(humidity, int) else f"{humidity or '72%'}"
    feels_str = f"{feels}°" if isinstance(feels, int) else f"{feels or '30°'}"
    icon = icon or "partly"

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    f_temp  = _font("VT323-Regular", 74)
    f_lbl   = _font("PressStart2P-Regular", 8)
    f_stat  = _font("VT323-Regular", 24)

    d.text((PAD, PAD + 4), city.upper(), font=f_lbl, fill=FG_MUTED, anchor="la")

    digits = "".join(ch for ch in str(temp) if (ch.isdigit() or ch == "-"))
    try: temp_i = int(digits)
    except ValueError: temp_i = 28
    accent = _temp_accent(temp_i)

    ty = 130
    num_str = f"{temp_i}"
    d.text((PAD, ty), num_str, font=f_temp, fill=accent, anchor="lm")

    ux = PAD + _tw(d, num_str, f_temp) + 4
    d.text((ux, ty - 20), "°C", font=_font("VT323-Regular", 36), fill=FG_MUTED, anchor="lm")

    d.text((PAD, ty + 46), cond, font=f_lbl, fill=BORDER_FG, anchor="la")
    _icon(d, W - PAD - 68, ty - 56, icon, accent)

    sy = H - 84
    d.line([PAD, sy, W - PAD, sy], fill=LINE, width=1)

    d.text((PAD, sy + 16), "HUMIDITY", font=f_lbl, fill=FG_MUTED, anchor="la")
    d.text((PAD, sy + 34), humidity_str.upper(), font=f_stat, fill=BORDER_FG, anchor="la")

    midx = W // 2 + 10
    d.text((midx, sy + 16), "FEELS LIKE", font=f_lbl, fill=FG_MUTED, anchor="la")
    d.text((midx, sy + 34), feels_str.upper(), font=f_stat, fill=BORDER_FG, anchor="la")
    return img
