"""
desky widget — Quote
240x320 RGB. A muted QUOTE label, the quote set in Press Start 2P at the
design's 2.1 line-height, and an amber attribution under a hairline.
Mirrors the `quote` state of DeskyPanel.
"""

import time
import requests
from PIL import Image, ImageDraw

from .theme import (
    W, H, PAD, BG, FG, FG_MUTED, ACCENT_WARM,
    label, hairline,
)

CACHE_TTL = 21600  # 6h — a quote is not news
_cache = {"quote": "LOADING...", "author": "SYSTEM", "ts": 0.0}

# Body box: between the top label and the hairline above the attribution.
TOP = 56
HAIRLINE_Y = 280
LINE_RATIO = 2.1  # design line-height for the quote body


def _fetch_quote():
    now = time.time()
    if (now - _cache["ts"]) < CACHE_TTL and _cache["quote"] != "LOADING...":
        return _cache["quote"], _cache["author"]

    # Stamp before the request so a failure can't spam the API every repaint.
    _cache["ts"] = now
    try:
        r = requests.get("https://dummyjson.com/quotes/random", timeout=4)
        if r.status_code == 200:
            data = r.json()
            _cache["quote"] = data.get("quote", "Stay retro.")
            _cache["author"] = data.get("author", "Unknown")
    except Exception:
        pass
    return _cache["quote"], _cache["author"]


def _wrap_px(text, font, max_w):
    """Greedy word-wrap by measured pixel width."""
    lines, cur = [], ""
    for word in text.split():
        trial = word if not cur else cur + " " + word
        if font.getlength(trial) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _fit(text, max_w, max_h):
    """Largest body size whose wrapped lines fit the box; clamp if none do."""
    for size in (8, 7, 6):
        f = label(size)
        lh = round(size * LINE_RATIO)
        wrapped = _wrap_px(text, f, max_w)
        if len(wrapped) * lh <= max_h:
            return wrapped, f, lh

    f = label(6)
    lh = round(6 * LINE_RATIO)
    wrapped = _wrap_px(text, f, max_w)
    keep = max(1, max_h // lh)
    lines = wrapped[:keep]
    if len(wrapped) > keep:
        lines[-1] = lines[-1][:-3] + "..."
    return lines, f, lh


def render() -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    d.text((PAD, PAD + 4), "QUOTE", font=label(), fill=FG_MUTED, anchor="la")

    quote, author = _fetch_quote()
    max_w = W - 2 * PAD
    lines, f_body, line_h = _fit(f'"{quote.upper()}"', max_w, HAIRLINE_Y - TOP - 8)

    # The design centres the body in the slack between label and hairline.
    y = TOP + max(0, (HAIRLINE_Y - TOP - 8 - len(lines) * line_h) // 2)
    for line in lines:
        d.text((PAD, y), line, font=f_body, fill=FG, anchor="la")
        y += line_h

    hairline(d, HAIRLINE_Y)
    d.text((W - PAD, H - PAD - 8), f"- {author.upper()}",
           font=label(), fill=ACCENT_WARM, anchor="ra")
    return img


if __name__ == "__main__":
    # ponytail: _fit is the only real logic — check short, long, and absurd.
    for name, q in (("short", "Stay retro."),
                    ("normal", "The impediment to action advances action. "
                               "What stands in the way becomes the way."),
                    ("long", "A ship in harbor is safe, but that is not what ships "
                             "are built for, and the sea will not apologise for the "
                             "weather it hands you on the way to somewhere better.")):
        _cache.update({"quote": q, "author": "Marcus Aurelius", "ts": time.time()})
        img = render()
        assert img.size == (W, H)
        img.save(f"out_quote_{name}.png")
    print("wrote out_quote_*.png")
