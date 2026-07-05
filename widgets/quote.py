"""
desky widget — Quote of the day
240x320 RGB. Quote-of-the-day from the backend, word-wrapped in SpaceGrotesk,
with an amber pixel quotation mark and a right-aligned author line.
"""

import os
import time as _time
import requests
from PIL import Image, ImageDraw, ImageFont

W, H = 240, 320
PAD = 22

BG       = (10, 10, 10)       # #0A0A0A
FG       = (242, 242, 242)
FG_MUTED = (107, 107, 107)
ACCENT   = (232, 201, 122)    # amber

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")
BACKEND = "https://web-production-12607.up.railway.app"
CACHE_TTL = 3600  # 1h — quote changes daily, don't hit backend every render
_cache = {"data": None, "ts": 0.0}


def _fetch_quote():
    now = _time.time()
    if _cache["data"] is not None and (now - _cache["ts"]) < CACHE_TTL:
        return _cache["data"]
    try:
        resp = requests.get(BACKEND + "/widget/quote", timeout=4)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data.get("quote"):
            _cache["data"] = data
            _cache["ts"] = now
            return data
    except Exception:
        pass
    return _cache["data"]  # stale (or None) on failure


def _font(name, size):
    try:
        return ImageFont.truetype(os.path.join(FONT_DIR, f"{name}.ttf"), size)
    except OSError:
        return ImageFont.load_default()


def _wrap(d, text, font, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if d.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _quote_mark(d, x, y, accent):
    # Two chunky pixel commas forming an opening quotation mark.
    for ox in (0, 20):
        d.rectangle([x + ox, y, x + ox + 12, y + 15], fill=accent)
        d.rectangle([x + ox, y + 15, x + ox + 6, y + 25], fill=accent)


def render(quote=None, author=None):
    if quote is None:
        data = _fetch_quote()
        if data:
            quote = data.get("quote")
            author = data.get("author")

    quote = quote or "Stay hungry, stay foolish."
    author = author or "Steve Jobs"

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    _quote_mark(d, PAD, PAD, ACCENT)

    max_w = W - 2 * PAD
    # Shrink until the quote fits in <= 4 lines, floor 14px; then hard-clamp.
    size = 22
    while size > 14:
        font = _font("SpaceGrotesk-Medium", size)
        lines = _wrap(d, quote, font, max_w)
        if len(lines) <= 4:
            break
        size -= 2
    else:
        font = _font("SpaceGrotesk-Medium", 14)
        lines = _wrap(d, quote, font, max_w)[:4]

    line_h = size + 8
    block_h = line_h * len(lines)
    ty = (H - block_h) // 2
    for i, ln in enumerate(lines):
        d.text((PAD, ty + i * line_h), ln, font=font, fill=FG, anchor="la")

    author_font = _font("SpaceGrotesk-Medium", 14)
    d.text((W - PAD, ty + block_h + 18), f"— {author}", font=author_font,
           fill=FG_MUTED, anchor="ra")

    return img


if __name__ == "__main__":
    # ponytail: offline self-check — render short + long quotes without a backend.
    render("Short one.", "Anon").save("out_quote.png")
    render("A" + " word" * 60, "Verbose Author").save("out_quote_long.png")
    print("wrote out_quote.png, out_quote_long.png")
