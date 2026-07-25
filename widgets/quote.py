"""
desky widget — Word of the Day / Quote
"""
import os
import requests
from PIL import Image, ImageDraw, ImageFont

W, H = 240, 320
PAD = 22

BG          = (10, 10, 10)
FG          = (242, 242, 242)
FG_MUTED    = (107, 107, 107)
ACCENT_WARM = (232, 201, 122)
LINE        = (34, 34, 34)

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")
_cache = {"quote": "LOADING...", "author": "SYSTEM", "ts": 0.0}

def _font(name, size):
    try: return ImageFont.truetype(os.path.join(FONT_DIR, f"{name}.ttf"), size)
    except OSError: return ImageFont.load_default()

def _fetch_quote():
    import time
    now = time.time()
    
    # 21600 seconds = 6 hours
    if (now - _cache["ts"]) < 21600 and _cache["quote"] != "LOADING...":
        return _cache["quote"], _cache["author"]
    
    # Update the timestamp BEFORE the request so we never spam the API on a failure
    _cache["ts"] = now
    
    try:
        # Swapped to DummyJSON: much more reliable and robust for IoT devices
        r = requests.get("https://dummyjson.com/quotes/random", timeout=4)
        if r.status_code == 200:
            data = r.json()
            _cache["quote"] = data.get("quote", "Stay retro.")
            _cache["author"] = data.get("author", "Unknown")
    except Exception:
        pass
        
    return _cache["quote"], _cache["author"]

def _wrap_px(text, font, max_w):
    """Greedy word-wrap by measured pixel width (VT323 isn't fixed-width, so a
    char count can't guarantee fit)."""
    lines, cur = [], ""
    for word in text.split():
        trial = word if not cur else cur + " " + word
        if font.getlength(trial) <= max_w:
            cur = trial
        else:
            if cur: lines.append(cur)
            cur = word
    if cur: lines.append(cur)
    return lines

def render() -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    f_lbl = _font("PressStart2P-Regular", 8)
    f_auth = _font("PressStart2P-Regular", 10)

    # Header
    d.text((PAD, PAD + 4), "INSPIRATION", font=f_lbl, fill=FG_MUTED, anchor="la")
    d.text((W - PAD, PAD + 4), "QUOTE", font=f_lbl, fill=ACCENT_WARM, anchor="ra")

    hy = PAD + 22
    d.line([PAD, hy, W - PAD, hy], fill=LINE, width=1)

    quote, author = _fetch_quote()

    # Fit the quote to the box between the header line and the author line.
    # Try decreasing font sizes; use the largest whose wrapped lines all fit.
    top      = 80
    bottom   = H - PAD - 20          # leave room for the author line
    max_w    = W - 2 * PAD
    text     = quote.upper()

    lines, f_body, line_h = [], None, 0
    for size in range(32, 13, -2):
        f = _font("VT323-Regular", size)
        lh = size                     # VT323 line advance ≈ point size
        wrapped = _wrap_px(text, f, max_w)
        if len(wrapped) * lh <= (bottom - top):
            lines, f_body, line_h = wrapped, f, lh
            break
    else:
        # Even at the smallest size it overflows — clamp and ellipsize.
        f_body = _font("VT323-Regular", 14)
        line_h = 14
        wrapped = _wrap_px(text, f_body, max_w)
        max_lines = max(1, (bottom - top) // line_h)
        lines = wrapped[:max_lines]
        if len(wrapped) > max_lines:
            lines[-1] = lines[-1][:-1] + "..."

    cy = top
    for line in lines:
        d.text((PAD, cy), line, font=f_body, fill=FG, anchor="la")
        cy += line_h

    # Author at the bottom
    d.text((W - PAD, H - PAD - 10), f"- {author.upper()}", font=f_auth, fill=ACCENT_WARM, anchor="ra")

    return img