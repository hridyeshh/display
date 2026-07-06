"""
desky widget — Word of the Day / Quote
"""
import os
import textwrap
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

def render() -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    f_lbl = _font("PressStart2P-Regular", 8)
    f_body = _font("VT323-Regular", 32)
    f_auth = _font("PressStart2P-Regular", 10)

    # Header
    d.text((PAD, PAD + 4), "INSPIRATION", font=f_lbl, fill=FG_MUTED, anchor="la")
    d.text((W - PAD, PAD + 4), "QUOTE", font=f_lbl, fill=ACCENT_WARM, anchor="ra")
    
    hy = PAD + 22
    d.line([PAD, hy, W - PAD, hy], fill=LINE, width=1)

    quote, author = _fetch_quote()

    # Wrap the text to fit the screen
    wrapped_lines = textwrap.wrap(quote, width=16)
    
    cy = 80
    for line in wrapped_lines:
        d.text((PAD, cy), line.upper(), font=f_body, fill=FG, anchor="la")
        cy += 32

    # Author at the bottom
    d.text((W - PAD, H - PAD - 10), f"- {author.upper()}", font=f_auth, fill=ACCENT_WARM, anchor="ra")

    return img