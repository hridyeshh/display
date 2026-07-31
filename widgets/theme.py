"""
desky — design tokens.

Python port of the design system's `tokens/colors.css` + `tokens/typography.css`
(_ds/design-system-62f93e7e-.../). Widgets import from here so the palette can't
drift per-file — every value below is the exact token, RGB-converted for Pillow.
"""

import os
from PIL import ImageFont

# Panel geometry — every screen is exactly one ILI9341 in portrait.
W, H = 240, 320
PAD = 22

# Surfaces
BG  = (10, 10, 10)      # --bg        #0A0A0A
BG2 = (17, 17, 17)      # --bg-2      #111111
BG3 = (26, 26, 26)      # --bg-3      #1A1A1A

# Text ramp
FG       = (242, 242, 242)  # --fg        #F2F2F2
FG_MUTED = (107, 107, 107)  # --fg-muted  #6B6B6B
FG_DIM   = (58, 58, 58)     # --fg-dim    #3A3A3A

# Hairlines
LINE = (34, 34, 34)     # --line      #222222

# Accents — one per widget context, never two on a screen.
ACCENT_WARM  = (232, 201, 122)  # --accent-warm   #E8C97A  clock / hot
ACCENT_COOL  = (122, 200, 232)  # --accent-cool   #7AC8E8  cold
ACCENT_MUSIC = (252, 60, 68)    # --accent-music  #FC3C44  now playing
ACCENT_DONE  = (91, 209, 122)   # --accent-done   #5BD17A  done / timer
ACCENT_AI    = (164, 122, 232)  # --accent-ai     #A47AE8  voice assistant
ACCENT_ERROR = ACCENT_MUSIC     # --accent-error

# Type — VT323 for hero numerals, Press Start 2P for every label and body line.
DISPLAY = "VT323-Regular"
BODY    = "PressStart2P-Regular"

SIZE_HERO  = 76   # --size-hero
SIZE_BODY  = 8    # --size-body
SIZE_LABEL = 8    # --size-label
SIZE_STAT  = 22   # --size-stat

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")


def font(name, size):
    """Load a bundled TTF, falling back to Pillow's bitmap font off-device."""
    try:
        return ImageFont.truetype(os.path.join(FONT_DIR, f"{name}.ttf"), size)
    except OSError:
        return ImageFont.load_default()


def hero(size=SIZE_HERO):
    return font(DISPLAY, size)


def label(size=SIZE_LABEL):
    return font(BODY, size)


def tw(d, text, f):
    return d.textlength(text, font=f)


def bar(d, x, y, width, frac, accent, height=3):
    """Bottom progress strip: a `--line` track with an accent fill."""
    d.rectangle([x, y, x + width, y + height - 1], fill=LINE)
    filled = int(width * max(0.0, min(1.0, frac)))
    if filled > 0:
        d.rectangle([x, y, x + filled, y + height - 1], fill=accent)


def hairline(d, y, x0=PAD, x1=W - PAD):
    d.line([x0, y, x1, y], fill=LINE, width=1)


if __name__ == "__main__":
    # ponytail: tokens are data — the only thing worth checking is that they
    # still match the CSS they were ported from.
    assert BG == (0x0A, 0x0A, 0x0A) and FG == (0xF2, 0xF2, 0xF2)
    assert ACCENT_WARM == (0xE8, 0xC9, 0x7A) and ACCENT_COOL == (0x7A, 0xC8, 0xE8)
    assert ACCENT_MUSIC == (0xFC, 0x3C, 0x44) and ACCENT_DONE == (0x5B, 0xD1, 0x7A)
    assert ACCENT_AI == (0xA4, 0x7A, 0xE8)
    assert (W, H, PAD) == (240, 320, 22)
    print("tokens ok")
