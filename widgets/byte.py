"""
desky widget — "Byte", the voice assistant character

Byte is not a configurable widget: it has no slot in CONFIG. main.py renders it
over VOICE_SCREEN while a voice interaction is live, then falls back to that
screen's normal widget once the backend returns to state 'idle'.

The character is drawn only with axis-aligned filled rectangles on a 16x16 unit
grid, ported from the design system's ai-sprite.js / pi/render.py.
"""
import os
import math
import time
from PIL import Image, ImageDraw, ImageFont

W, H = 240, 320
PAD = 22

BG        = (10, 10, 10)      # #0A0A0A
FG        = (242, 242, 242)   # #F2F2F2
FG_MUTED  = (107, 107, 107)   # #6B6B6B
FG_DIM    = (58, 58, 58)      # #3A3A3A
LINE      = (34, 34, 34)      # #222222

ACCENT_AI    = (164, 122, 232)  # #A47AE8  voice assistant purple
AI_DARK      = (110, 79, 160)   # #6E4FA0  Byte's shadow shell
ACCENT_ERROR = (252, 60, 68)    # #FC3C44  error red
ERR_DARK     = (142, 35, 41)    # #8E2329  Byte's shadow shell (error)

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")

# Animation cadence: one frame per 320ms, derived from the wall clock so every
# render is self-contained (no frame counter to thread through main.py).
FRAME_MS = 0.32


def _font(name, size):
    try:
        return ImageFont.truetype(os.path.join(FONT_DIR, f"{name}.ttf"), size)
    except OSError:
        return ImageFont.load_default()


VT, P2P = "VT323-Regular", "PressStart2P-Regular"
_FONTS = {
    "hero":   _font(VT, 52),   # spoken-answer hero (short answers only)
    "mid":    _font(VT, 30),   # medium answer text
    "body":   _font(P2P, 8),   # primary body text
    "label":  _font(P2P, 7),   # tiny caps labels
    "tiny":   _font(P2P, 6),   # smallest text
}


def _frame():
    return int(time.time() / FRAME_MS)


def _tw(d, text, font):
    return d.textlength(text, font=font)


def tracked(d, xy, text, font, fill, tracking=0, anchor="la"):
    """Draw text with extra letter-spacing (Pillow has no native tracking).

    anchor's horizontal part ('l'/'m'/'r') applies to the whole string.
    """
    chars = list(text)
    widths = [_tw(d, c, font) for c in chars]
    total = sum(widths) + tracking * max(len(chars) - 1, 0)
    x, y = xy
    if anchor[0] == "m":
        x -= total / 2
    elif anchor[0] == "r":
        x -= total
    for c, w in zip(chars, widths):
        d.text((x, y), c, font=font, fill=fill)
        x += w + tracking
    return total


def _wrap(d, text, font, max_w, tracking=0, max_lines=None):
    """Greedy word wrap to max_w px. Long single words are hard-split."""
    words, lines, cur = str(text).split(), [], ""

    def width(s):
        return sum(_tw(d, c, font) for c in s) + tracking * max(len(s) - 1, 0)

    for word in words:
        probe = word if not cur else cur + " " + word
        if width(probe) <= max_w:
            cur = probe
            continue
        if cur:
            lines.append(cur)
        while width(word) > max_w and len(word) > 1:      # word alone overflows
            cut = len(word)
            while cut > 1 and width(word[:cut]) > max_w:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        cur = word
    if cur:
        lines.append(cur)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = (lines[-1][:-1] + "…") if lines[-1] else "…"
    return lines


def _canvas():
    img = Image.new("RGB", (W, H), BG)
    return img, ImageDraw.Draw(img)


# ---------------------------------------------------------------------------
# The character
# ---------------------------------------------------------------------------
def byte_rects(state, f=0):
    """[(x, y, w, h, color), ...] in 16x16 grid units. Mirror of ai-sprite.js."""
    err = state == "error"
    shell = ACCENT_ERROR if err else ACCENT_AI
    dark = ERR_DARK if err else AI_DARK
    r = []

    # antenna — pulses while listening, blinks while thinking
    if state == "listening":
        r.append((6, 0, 4, 2, shell) if f % 2 == 0 else (7, 0, 2, 2, shell))
    elif state == "thinking":
        r.append((7, 0, 2, 2, shell if f % 4 == 0 else dark))
    else:
        r.append((7, 0, 2, 2, dark if err else shell))
    r.append((7, 2, 2, 3, dark))

    r.append((1, 5, 14, 11, shell))                       # head
    r += [(0, 8, 1, 4, dark), (15, 8, 1, 4, dark)]        # ears
    r.append((3, 7, 10, 6, BG))                           # visor

    if err:                                               # X eyes
        for ex in (4, 8):
            for dx, dy in ((0, 0), (2, 0), (1, 1), (0, 2), (2, 2)):
                r.append((ex + dx, 9 + dy, 1, 1, FG))
    elif state == "thinking":                             # squint, scanning
        s = [0, 1, 1, 0, -1, -1][f % 6]
        r += [(5 + s, 10, 2, 1, FG), (9 + s, 10, 2, 1, FG)]
    elif state == "listening" and f % 9 == 8:             # blink
        r += [(5, 10, 2, 1, FG), (9, 10, 2, 1, FG)]
    else:                                                 # wide eyes
        r += [(5, 9, 2, 3, FG), (9, 9, 2, 3, FG)]

    if err:                                               # frown
        r += [(5, 14, 2, 1, BG), (7, 15, 2, 1, BG), (9, 14, 2, 1, BG)]
    elif state == "speaking":                             # mouth bar opens
        r.append((5, 14, 6, [1, 2, 2, 1, 2, 1][f % 6], BG))
    elif state == "thinking":
        r.append((7, 14, 2, 1, BG))
    else:
        r.append((6, 14, 4, 1, BG))
    return r


def draw_byte(d, ox, oy, state, p=8, f=0):
    """Draw Byte with its top-left at (ox, oy); p = unit size in px."""
    for x, y, w, h, col in byte_rects(state, f):
        d.rectangle([ox + x * p, oy + y * p, ox + (x + w) * p - 1, oy + (y + h) * p - 1], fill=col)


def _seg_bar(d, x, y, w, h, n, on, accent):
    """Segmented level bar: n cells across (x, y, w, h), first `on` filled."""
    cw = (w - (n - 1) * 2) / n
    for i in range(n):
        cx = x + i * (cw + 2)
        d.rectangle([round(cx), y, round(cx + cw), y + h], fill=accent if i < on else LINE)


def _chrome(d, state_label, accent):
    """Shared frame: DESKY / VOICE top-left, state label top-right."""
    tracked(d, (PAD - 4, 16), "DESKY", _FONTS["label"], FG_MUTED, tracking=1)
    tracked(d, (PAD - 4, 30), "VOICE", _FONTS["label"], FG_MUTED, tracking=1)
    tracked(d, (W - PAD + 4, 16), state_label, _FONTS["label"], accent, tracking=1, anchor="ra")


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------
def render_byte_listening(level=8):
    """Wake word heard, mic open: wide eyes, pulsing antenna, level meter."""
    f = _frame()
    img, d = _canvas()
    _chrome(d, "LISTENING", ACCENT_AI)
    draw_byte(d, (W - 16 * 8) // 2, 96, "listening", p=8, f=f)

    for i in range(5):                                   # equalizer flanks head
        lw = 4 + int(abs(math.sin(f * 0.55 + i * 0.9)) * 10)
        yy = 118 + i * 12
        d.rectangle([PAD - 4, yy, PAD - 4 + lw, yy + 2], fill=ACCENT_AI)
        d.rectangle([W - PAD + 4 - lw, yy, W - PAD + 4, yy + 2], fill=ACCENT_AI)

    tracked(d, (W // 2, 250), '"HEY DESKY"', _FONTS["body"], FG, tracking=1, anchor="ma")
    tracked(d, (W // 2, 270), "ASK ME ANYTHING", _FONTS["label"], FG_DIM, tracking=1, anchor="ma")
    _seg_bar(d, PAD, H - PAD - 4, W - 2 * PAD, 4, 12, int(level), ACCENT_AI)
    return img


def render_byte_thinking(question=""):
    """Question sent, waiting on Claude: squinting scan eyes, question, scan bar."""
    f = _frame()
    img, d = _canvas()
    _chrome(d, "SEARCHING", ACCENT_AI)
    draw_byte(d, (W - 16 * 8) // 2, 96, "thinking", p=8, f=f)

    for i in range(3):                                   # thinking dots
        dx = W // 2 - 12 + i * 11
        d.rectangle([dx, 78, dx + 5, 83], fill=ACCENT_AI if f % 4 == i else FG_DIM)

    d.rectangle([PAD - 4, 244, W - PAD + 4, 244], fill=LINE)
    tracked(d, (PAD - 4, 256), "YOU ASKED", _FONTS["label"], FG_MUTED, tracking=1)
    y = 274
    for ln in _wrap(d, question.upper(), _FONTS["body"], W - 2 * (PAD - 4), tracking=1, max_lines=2):
        tracked(d, (PAD - 4, y), ln, _FONTS["body"], FG, tracking=1)
        y += 15

    x0 = ((f * 14) % (W - 2 * PAD + 56)) - 56            # indeterminate scan block
    d.rectangle([PAD, H - PAD - 4, W - PAD, H - PAD], fill=LINE)
    d.rectangle([max(PAD, PAD + x0), H - PAD - 4,
                 min(W - PAD, PAD + x0 + 56), H - PAD], fill=ACCENT_AI)
    return img


def render_byte_speaking(answer="", elapsed=None, source=None):
    """Answer ready: Byte shrinks to the corner, the answer takes the screen."""
    f = _frame()
    img, d = _canvas()
    _chrome(d, "SPEAKING", ACCENT_AI)
    if elapsed is not None:
        tracked(d, (W - PAD + 4, 30), f"{int(elapsed)}S", _FONTS["label"], FG_MUTED,
                tracking=1, anchor="ra")

    draw_byte(d, PAD - 4, 46, "speaking", p=2, f=f)

    # Size the answer to its length: a bare "4" gets the hero treatment, a
    # sentence wraps as body text. Anything longer is clipped rather than
    # spilling off-screen — the answer is also spoken, so the screen is a recap.
    text = str(answer).strip()
    max_w = W - 2 * (PAD - 4)
    if len(text) <= 6:
        d.text((PAD - 4, 100), text, font=_FONTS["hero"], fill=FG)
    elif len(text) <= 22:
        y = 104
        for ln in _wrap(d, text, _FONTS["mid"], max_w, max_lines=3):
            d.text((PAD - 4, y), ln, font=_FONTS["mid"], fill=FG)
            y += 32
    else:
        y = 96
        for ln in _wrap(d, text.upper(), _FONTS["body"], max_w, tracking=1, max_lines=9):
            tracked(d, (PAD - 4, y), ln, _FONTS["body"], FG, tracking=1)
            y += 16

    d.rectangle([PAD - 4, 258, W - PAD + 4, 258], fill=LINE)
    for i in range(22):                                  # waveform
        bh = 2 + int(abs(math.sin(f * 0.5 + i * 0.7)) * 14)
        bx = PAD - 4 + i * 9
        d.rectangle([bx, 286 - bh, bx + 6, 286], fill=ACCENT_AI)
    if source:
        tracked(d, (PAD - 4, 296), str(source).upper(), _FONTS["label"], FG_DIM, tracking=1)
    tracked(d, (W - PAD + 4, 296), 'SAY "STOP"', _FONTS["label"], FG_DIM, tracking=1, anchor="ra")
    return img


def render_byte_error(attempt=None, attempts=3, countdown=1.0):
    """Something failed: X eyes, red shell, static in the visor, countdown bar."""
    f = _frame()
    img, d = _canvas()
    _chrome(d, "NO MATCH", ACCENT_ERROR)

    ox, oy, p = (W - 16 * 8) // 2, 96, 8
    draw_byte(d, ox, oy, "error", p=p, f=f)
    # Checkered static drifting across the visor, kept clear of the X eyes.
    for gx in range(3, 13):
        for gy in range(7, 13):
            if (gx + gy + f) % 3 == 0 and not (4 <= gx <= 10 and 9 <= gy <= 11):
                d.rectangle([ox + gx * p, oy + gy * p,
                             ox + (gx + 1) * p - 1, oy + (gy + 1) * p - 1], fill=ERR_DARK)

    tracked(d, (W // 2, 236), "DIDN'T CATCH", _FONTS["body"], FG, tracking=1, anchor="ma")
    tracked(d, (W // 2, 252), "THAT", _FONTS["body"], FG, tracking=1, anchor="ma")
    tracked(d, (W // 2, 274), 'SAY "HEY DESKY"', _FONTS["label"], FG_MUTED, tracking=1, anchor="ma")
    tracked(d, (W // 2, 286), "AND TRY AGAIN", _FONTS["label"], FG_MUTED, tracking=1, anchor="ma")
    d.rectangle([PAD, H - PAD - 4, W - PAD, H - PAD], fill=LINE)
    if attempt:                                  # below the bar, not across it
        tracked(d, (W // 2, H - PAD + 6), f"ATTEMPT {attempt} OF {attempts}",
                _FONTS["tiny"], FG_DIM, tracking=1, anchor="ma")
    pct = max(0.0, min(1.0, float(countdown)))
    d.rectangle([PAD, H - PAD - 4, PAD + int((W - 2 * PAD) * pct), H - PAD], fill=ACCENT_ERROR)
    return img


# ---------------------------------------------------------------------------
# Dispatcher used by main.py
# ---------------------------------------------------------------------------
def render(state, question="", answer="", since=None, attempt=None, source=None):
    """Render whichever Byte state the backend reports.

    `since` is the epoch seconds of the last state change, used for the elapsed
    counter and the error countdown bar. Returns None for 'idle' (or any state
    we don't draw) so the caller falls through to the screen's normal widget.
    """
    state = (state or "idle").lower()
    elapsed = (time.time() - since) if since else None
    if state == "listening":
        # Decorative level: this device has no mic input wired yet.
        return render_byte_listening(level=6 + int(abs(math.sin(_frame() * 0.5)) * 6))
    if state == "thinking":
        return render_byte_thinking(question)
    if state in ("speaking", "answered"):
        return render_byte_speaking(answer, elapsed=elapsed, source=source)
    if state == "error":
        remaining = 1.0 if elapsed is None else max(0.0, 1.0 - elapsed / 4.0)
        return render_byte_error(attempt=attempt, countdown=remaining)
    return None


if __name__ == "__main__":
    # Self-check: every state renders, the dispatcher routes, idle falls through.
    assert render("idle") is None, "idle must fall through to the normal widget"
    assert render("") is None and render(None) is None
    for st in ("listening", "thinking", "speaking", "answered", "error"):
        img = render(st, question="what is 2+2", answer="4", since=time.time() - 3)
        assert img is not None and img.size == (W, H), st
    # Wrapping must never exceed the drawn width or drop the max_lines cap.
    _img, _d = _canvas()
    _lines = _wrap(_d, "A" * 400, _FONTS["body"], 100, tracking=1, max_lines=4)
    assert len(_lines) == 4, _lines

    render_byte_listening().save("out_byte_listening.png")
    render_byte_thinking("WILL IT RAIN IN BENGALURU TODAY?").save("out_byte_thinking.png")
    render_byte_speaking("4", elapsed=3).save("out_byte_speaking_short.png")
    render_byte_speaking(
        "The NIFTY 50 is trading 0.18% lower today at 23,916, with auto stocks "
        "leading gains while banking drags the index down.",
        elapsed=6, source="web search").save("out_byte_speaking_long.png")
    render_byte_error(attempt=2, countdown=0.6).save("out_byte_error.png")
    print("ok — wrote out_byte_*.png")
