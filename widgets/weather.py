"""
desky widget — Weather
240x320 RGB. Hero temperature in the context accent, a shape-only icon built
from ellipse/line/rectangle, and a humidity/feels stat row under a hairline.
Mirrors the `weather` state of DeskyPanel.
"""

import time as _time
import requests
from PIL import Image, ImageDraw

from .theme import (
    W, H, PAD, BG, FG, FG_MUTED, ACCENT_WARM, ACCENT_COOL,
    hero, label, tw, hairline,
)

BACKEND = "https://web-production-12607.up.railway.app"
CACHE_TTL = 60
_cache = {"data": None, "ts": 0.0}

# Icon box: the design's 16-unit viewBox rendered into a 56px square.
ICON_PX = 56
_S = ICON_PX / 16.0

TEMP_BASELINE = 165
ICON_X = W - PAD - ICON_PX
ICON_Y = TEMP_BASELINE - 54


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
    except Exception:
        pass
    return _cache["data"]


def _truncate(d, text, font, max_w):
    if tw(d, text, font) <= max_w:
        return text
    while text and tw(d, text + "..", font) > max_w:
        text = text[:-1]
    return text + ".."


# ── icon primitives: unit coords (0..16) → pixels in the icon box ────────────
def _circ(d, ox, oy, cx, cy, r, fill):
    d.ellipse([ox + (cx - r) * _S, oy + (cy - r) * _S,
               ox + (cx + r) * _S, oy + (cy + r) * _S], fill=fill)


def _ln(d, ox, oy, x1, y1, x2, y2, fill, w):
    d.line([ox + x1 * _S, oy + y1 * _S, ox + x2 * _S, oy + y2 * _S],
           fill=fill, width=max(1, round(w * _S)))


def _rect(d, ox, oy, x, y, w, h, fill):
    d.rectangle([ox + x * _S, oy + y * _S,
                 ox + (x + w) * _S - 1, oy + (y + h) * _S - 1], fill=fill)


def _cloud(d, ox, oy, c):
    _circ(d, ox, oy, 6, 8, 3.2, c)
    _circ(d, ox, oy, 10.2, 8.4, 2.6, c)
    _rect(d, ox, oy, 3, 9, 10, 3.2, c)


_SUN_RAYS = [(8, 0.6, 8, 3), (8, 13, 8, 15.4), (0.6, 8, 3, 8), (13, 8, 15.4, 8),
             (2.6, 2.6, 4.3, 4.3), (11.7, 11.7, 13.4, 13.4),
             (11.7, 4.3, 13.4, 2.6), (2.6, 13.4, 4.3, 11.7)]
_DROP_X = (4.5, 8, 11.5)


def _icon(d, ox, oy, kind, c):
    """Draw a weather glyph from ellipse/line/rectangle only, so the browser
    mock and the Pillow render stay structurally identical."""
    if kind == "sun":
        _circ(d, ox, oy, 8, 8, 3.4, c)
        for x1, y1, x2, y2 in _SUN_RAYS:
            _ln(d, ox, oy, x1, y1, x2, y2, c, 1.4)
        return
    if kind == "partly":
        _circ(d, ox, oy, 11, 4.6, 2.4, c)
        _cloud(d, ox, oy, c)
        return
    if kind == "fog":
        _cloud(d, ox, oy, FG_MUTED)
        for y in (12.6, 14.4):
            _ln(d, ox, oy, 2.6, y, 13.4, y, c, 1.2)
        return

    _cloud(d, ox, oy, c)
    if kind == "rain":
        for x in _DROP_X:
            _ln(d, ox, oy, x, 13, x - 0.8, 15.6, c, 1.3)
    elif kind == "storm":
        _ln(d, ox, oy, 8.6, 12.6, 6.6, 15.8, ACCENT_WARM, 1.4)
        _ln(d, ox, oy, 6.6, 14.2, 9.4, 14.2, ACCENT_WARM, 1.4)
    elif kind == "snow":
        for x in _DROP_X:
            _rect(d, ox, oy, x - 0.7, 13.4, 1.4, 1.4, c)


_ICON_MAP = {
    "clear": "sun", "sun": "sun", "sunny": "sun",
    "cloud": "cloud", "cloudy": "cloud", "overcast": "cloud", "clouds": "cloud",
    "partly": "partly", "partly cloudy": "partly", "few clouds": "partly",
    "scattered clouds": "partly", "broken clouds": "partly",
    "rain": "rain", "drizzle": "rain", "shower": "rain", "shower rain": "rain",
    "storm": "storm", "thunderstorm": "storm", "thunder": "storm",
    "snow": "snow", "sleet": "snow",
    "fog": "fog", "mist": "fog", "haze": "fog", "smoke": "fog",
}

# The design uses one accent per screen: cyan when it's cold, amber otherwise.
COLD_C = 20


def render(city=None, temp=None, cond=None, humidity=None, feels=None, icon=None):
    if temp is None and cond is None and icon is None:
        data = _fetch_weather()
        if data:
            temp = data.get("temp")
            cond = data.get("condition")
            humidity = data.get("humidity")
            icon = _ICON_MAP.get(
                str(data.get("icon") or data.get("condition")).strip().lower(), "cloud")
            feels = data.get("feels_like") or data.get("feels")

    city = city or "BENGALURU"
    cond = str(cond or "PARTLY CLOUDY").upper()
    icon = icon or "partly"

    digits = "".join(ch for ch in str(temp) if (ch.isdigit() or ch == "-"))
    try:
        temp_i = int(digits)
    except ValueError:
        temp_i = 28
    accent = ACCENT_COOL if temp_i <= COLD_C else ACCENT_WARM

    humidity_str = f"{humidity}%" if isinstance(humidity, int) else f"{humidity or '72%'}"
    feels_str = f"{feels}°" if isinstance(feels, int) else f"{feels or str(temp_i + 2) + '°'}"

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    f_temp = hero(72)
    f_unit = hero(30)
    f_lbl = label()

    d.text((PAD, PAD + 4), city.upper(), font=f_lbl, fill=FG_MUTED, anchor="la")

    num = f"{temp_i}"
    d.text((PAD, TEMP_BASELINE), num, font=f_temp, fill=accent, anchor="ls")
    d.text((PAD + tw(d, num, f_temp) + 2, TEMP_BASELINE), "°C",
           font=f_unit, fill=accent, anchor="ls")

    _icon(d, ICON_X, ICON_Y, icon, accent)

    d.text((PAD, 238), _truncate(d, cond, f_lbl, W - 2 * PAD), font=f_lbl, fill=FG, anchor="la")

    hairline(d, 258)
    d.text((PAD, 266), "HUMIDITY", font=f_lbl, fill=FG_MUTED, anchor="la")
    d.text((PAD, 282), humidity_str.upper(), font=f_lbl, fill=FG, anchor="la")
    d.text((W - PAD, 266), "FEELS", font=f_lbl, fill=FG_MUTED, anchor="ra")
    d.text((W - PAD, 282), feels_str.upper(), font=f_lbl, fill=FG, anchor="ra")
    return img


if __name__ == "__main__":
    # ponytail: one PNG per icon kind — the icon branch is the only real logic.
    for k in ("sun", "cloud", "partly", "rain", "storm", "snow", "fog"):
        render("Bengaluru", 28, k.upper(), 72, 30, k).save(f"out_weather_{k}.png")
    print("wrote out_weather_<kind>.png")
