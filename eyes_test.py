#!/usr/bin/env python3
"""
eyes_test.py — Phase B throwaway perf probe: can a Pi Zero 2W animate eyes?

Standalone on purpose. Nothing imports this, it is not a widget, it is in no
service. It drives screen2 (/dev/spidev0.1, DC=24, RST shared on 25) the exact
way DisplayPanel does in main.py — PIL -> numpy -> RGB565 big-endian ->
spi.writebytes2 — and reports the fps and CPU that actually came out.

    sudo systemctl stop desky        # required: desky owns GPIO 24 and this bus
    python3 eyes_test.py --mode full  --secs 30
    python3 eyes_test.py --mode dirty --secs 30 --speed 32000000
    sudo systemctl start desky

The three modes draw the identical picture and differ only in how many pixels
go over the wire, so the gap between them is pure SPI cost:

    full   whole 240x320 panel      153600 B/frame
    band   the eye band only       ~ 60000 B/frame
    dirty  two eye boxes           ~ 33000 B/frame

At 16 MHz the wire alone costs 76.8 ms for a full frame, i.e. 13 fps before a
single pixel is drawn. That number, not the CPU, is what this test is really
about.
"""
import argparse
import math
import os
import random
import subprocess
import sys
import time

import numpy as np
from PIL import Image, ImageDraw

W, H = 240, 320
BG      = (10, 10, 10)
EYE     = (164, 122, 232)   # ACCENT_AI, same purple Byte uses
PUPIL   = (10, 10, 10)
SPARK   = (242, 242, 242)

# Eye geometry, in panel pixels.
CY      = 158
CX      = (72, 168)
EW, EH  = 78, 96            # sclera size, fully open
PR      = 21                # pupil radius
DX, DY  = 15, 11            # how far the pupil may wander from centre

EYE_BOX = [(CX[0] - EW // 2 - 3, CY - EH // 2 - 3, CX[0] + EW // 2 + 3, CY + EH // 2 + 3),
           (CX[1] - EW // 2 - 3, CY - EH // 2 - 3, CX[1] + EW // 2 + 3, CY + EH // 2 + 3)]
BAND_BOX = (0, CY - EH // 2 - 6, W - 1, CY + EH // 2 + 6)

TICKS = os.sysconf("SC_CLK_TCK")


# ---------------------------------------------------------------------------
# panel — the parts of main.py's DisplayPanel this needs, plus a windowed push
# ---------------------------------------------------------------------------
class Panel:
    def __init__(self, port=0, device=1, dc=24, rst=25, speed=16000000):
        import lgpio
        import spidev
        self.lgpio = lgpio
        self.dc = dc
        self.gpio = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_output(self.gpio, dc, 0)
        # screen2 has rst=None in main.py: it shares screen1's reset line, which
        # main() toggles once at startup. Same toggle here, or the panel keeps
        # whatever state desky left it in.
        if rst is not None:
            lgpio.gpio_claim_output(self.gpio, rst, 1)
            lgpio.gpio_write(self.gpio, rst, 0)
            time.sleep(0.05)
            lgpio.gpio_write(self.gpio, rst, 1)
            time.sleep(0.15)
        self.spi = spidev.SpiDev()
        self.spi.open(port, device)
        self.spi.max_speed_hz = speed
        self.spi.mode = 0
        self._init()

    def cmd(self, c):
        self.lgpio.gpio_write(self.gpio, self.dc, 0)
        self.spi.writebytes([c])

    def dat(self, data):
        self.lgpio.gpio_write(self.gpio, self.dc, 1)
        if isinstance(data, int):
            data = [data]
        self.spi.writebytes2(data)

    def _init(self):
        self.cmd(0x01); time.sleep(0.15)
        self.cmd(0x28)
        self.cmd(0xCF); self.dat([0x00, 0xC1, 0x30])
        self.cmd(0xED); self.dat([0x64, 0x03, 0x12, 0x81])
        self.cmd(0xE8); self.dat([0x85, 0x00, 0x78])
        self.cmd(0xCB); self.dat([0x39, 0x2C, 0x00, 0x34, 0x02])
        self.cmd(0xF7); self.dat([0x20])
        self.cmd(0xEA); self.dat([0x00, 0x00])
        self.cmd(0xC0); self.dat([0x23])
        self.cmd(0xC1); self.dat([0x10])
        self.cmd(0xC5); self.dat([0x3E, 0x28])
        self.cmd(0xC7); self.dat([0x86])
        self.cmd(0x36); self.dat([0xE0])        # flip_180, as main.py sets it
        self.cmd(0x3A); self.dat([0x55])
        self.cmd(0xB1); self.dat([0x00, 0x18])
        self.cmd(0xB6); self.dat([0x08, 0x82, 0x27])
        self.cmd(0xF2); self.dat([0x00])
        self.cmd(0x26); self.dat([0x01])
        self.cmd(0xE0); self.dat([0x0F, 0x31, 0x2B, 0x0C, 0x0E, 0x08, 0x4E, 0xF1, 0x37, 0x07, 0x10, 0x03, 0x0E, 0x09, 0x00])
        self.cmd(0xE1); self.dat([0x00, 0x0E, 0x14, 0x03, 0x11, 0x07, 0x31, 0xC1, 0x48, 0x08, 0x0F, 0x0C, 0x31, 0x36, 0x0F])
        self.cmd(0x11); time.sleep(0.12)
        self.cmd(0x29); time.sleep(0.02)

    def _window(self, x0, y0, x1, y1):
        self.cmd(0x2A); self.dat([x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF])
        self.cmd(0x2B); self.dat([y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF])
        self.cmd(0x2C)

    def push(self, img, box=None):
        """Push the whole image, or just `box` = (x0, y0, x1, y1) inclusive.

        Same coordinate space as the full-frame write in main.py, so a crop
        lands where it sits in the full picture.
        """
        if box is None:
            x0, y0, x1, y1 = 0, 0, W - 1, H - 1
            sub = img
        else:
            x0, y0, x1, y1 = box
            sub = img.crop((x0, y0, x1 + 1, y1 + 1))
        self._window(x0, y0, x1, y1)
        self.dat(to565(sub))
        return (x1 - x0 + 1) * (y1 - y0 + 1) * 2


def to565(img):
    a = np.asarray(img, dtype=np.uint16)
    r = (a[:, :, 0] & 0xF8) << 8
    g = (a[:, :, 1] & 0xFC) << 3
    b = (a[:, :, 2]) >> 3
    return (r | g | b).astype(">u2").tobytes()


# ---------------------------------------------------------------------------
# the eyes
# ---------------------------------------------------------------------------
class Eyes:
    """Pupil position + blink state. Pure math, no PIL, so it self-tests."""

    BLINK_SEC = 0.16

    def __init__(self, seed=7):
        self.rng = random.Random(seed)
        self.x = self.y = 0.0          # current pupil offset
        self.tx = self.ty = 0.0        # where it is drifting to
        self.next_look = 0.0
        self.next_blink = 2.0
        self.blink_t = -1.0
        self.open = 1.0

    def step(self, t, dt):
        if t >= self.next_look:                     # new idle-wander target
            self.tx = self.rng.uniform(-DX, DX)
            self.ty = self.rng.uniform(-DY, DY)
            self.next_look = t + self.rng.uniform(0.9, 2.6)
        # Exponential ease toward the target: framerate-independent, so the
        # drift looks the same at 8 fps and at 40.
        k = 1.0 - math.exp(-dt * 4.0)
        self.x += (self.tx - self.x) * k
        self.y += (self.ty - self.y) * k

        if self.blink_t < 0 and t >= self.next_blink:
            self.blink_t = t
            self.next_blink = t + self.rng.uniform(2.4, 6.5)
        if self.blink_t >= 0:
            p = (t - self.blink_t) / self.BLINK_SEC
            if p >= 1.0:
                self.blink_t, self.open = -1.0, 1.0
            else:                                   # down and back up
                self.open = abs(p * 2.0 - 1.0)
        return self.x, self.y, self.open


def draw(d, ex, ey, openness):
    d.rectangle(BAND_BOX, fill=BG)
    for cx in CX:
        eh = max(3, int(EH * openness))
        d.ellipse([cx - EW // 2, CY - eh // 2, cx + EW // 2, CY + eh // 2], fill=EYE)
        if openness > 0.3:
            ph = max(2, int(PR * openness))
            px, py = cx + ex, CY + ey * openness
            d.ellipse([px - PR, py - ph, px + PR, py + ph], fill=PUPIL)
            if openness > 0.8:                      # catchlight, sells the gloss
                d.ellipse([px + 5, py - ph + 3, px + 11, py - ph + 9], fill=SPARK)


# ---------------------------------------------------------------------------
# instrumentation
# ---------------------------------------------------------------------------
def cpu_totals():
    with open("/proc/stat") as f:
        p = f.readline().split()[1:]
    v = [int(x) for x in p]
    return sum(v), v[3] + v[4]                      # total, idle+iowait


def proc_ticks(pid="self"):
    try:
        with open(f"/proc/{pid}/stat") as f:
            p = f.read().rsplit(") ", 1)[1].split()
        return int(p[11]) + int(p[12])              # utime + stime
    except Exception:
        return None


def find_pid(needle):
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/cmdline", "rb") as f:
                if needle.encode() in f.read():
                    return d
        except Exception:
            pass
    return None


def temp_c():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read()) / 1000.0
    except Exception:
        return float("nan")


def throttled():
    try:
        return subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                              text=True, timeout=2).stdout.strip()
    except Exception:
        return "?"


def pct(vals, q):
    if not vals:
        return float("nan")
    s = sorted(vals)
    return s[min(len(s) - 1, int(len(s) * q))]


# ---------------------------------------------------------------------------
def run(args):
    panel = Panel(speed=args.speed)
    canvas = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(canvas)
    eyes = Eyes()

    box = {"full": None, "band": BAND_BOX}.get(args.mode)
    panel.push(canvas)                              # black the panel once

    log = open(args.log, "a", buffering=1)
    log.write(f"\n# eyes_test mode={args.mode} speed={args.speed} fps_cap={args.fps} "
              f"secs={args.secs} start={time.strftime('%F %T')}\n")
    log.write("t,fps,draw_ms,spi_ms,frame_ms,sys_cpu_pct,self_cpu_pct,voice_cpu_pct,temp_c\n")

    voice_pid = find_pid("wake_word.py")
    print(f"[eyes] mode={args.mode} spi={args.speed/1e6:.0f}MHz cap={args.fps or 'none'} "
          f"voice_pid={voice_pid or '-'} temp={temp_c():.1f}C", flush=True)

    t0 = time.perf_counter()
    end = t0 + args.secs
    last = t0
    win_start, win_frames = t0, 0
    c_tot, c_idle = cpu_totals()
    s_ticks = proc_ticks()
    v_ticks = proc_ticks(voice_pid) if voice_pid else None
    draw_ms, spi_ms, frame_ms = [], [], []
    fps_hist, sys_hist, self_hist, voice_hist = [], [], [], []
    frames = misses = bytes_out = 0
    period = (1.0 / args.fps) if args.fps else 0.0
    next_deadline = t0 + period

    while True:
        now = time.perf_counter()
        if now >= end:
            break
        dt, last = now - last, now

        a = time.perf_counter()
        draw(d, *eyes.step(now - t0, dt))
        b = time.perf_counter()
        if args.mode == "dirty":
            bytes_out += panel.push(canvas, EYE_BOX[0]) + panel.push(canvas, EYE_BOX[1])
        else:
            bytes_out += panel.push(canvas, box)
        c = time.perf_counter()

        draw_ms.append((b - a) * 1e3)
        spi_ms.append((c - b) * 1e3)
        frame_ms.append((c - a) * 1e3)
        frames += 1
        win_frames += 1

        if period:
            slack = next_deadline - time.perf_counter()
            if slack > 0:
                time.sleep(slack)
            else:
                misses += 1
            next_deadline += period

        if c - win_start >= 1.0:                    # one row per second
            n_tot, n_idle = cpu_totals()
            sys_pct = 100.0 * (1 - (n_idle - c_idle) / max(1, n_tot - c_tot))
            c_tot, c_idle = n_tot, n_idle
            n_s = proc_ticks()
            self_pct = 100.0 * (n_s - s_ticks) / TICKS / (c - win_start)
            s_ticks = n_s
            voice_pct = float("nan")
            if voice_pid:
                n_v = proc_ticks(voice_pid)
                if n_v is not None and v_ticks is not None:
                    voice_pct = 100.0 * (n_v - v_ticks) / TICKS / (c - win_start)
                v_ticks = n_v
            fps = win_frames / (c - win_start)
            dm, sm = np.mean(draw_ms[-win_frames:]), np.mean(spi_ms[-win_frames:])
            tc = temp_c()
            fps_hist.append(fps); sys_hist.append(sys_pct); self_hist.append(self_pct)
            if voice_pct == voice_pct:
                voice_hist.append(voice_pct)
            print(f"{c - t0:6.1f}s  fps {fps:5.1f}  draw {dm:5.1f}ms  spi {sm:5.1f}ms  "
                  f"sys {sys_pct:5.1f}%  self {self_pct:5.1f}%  voice {voice_pct:5.1f}%  {tc:.1f}C",
                  flush=True)
            log.write(f"{c-t0:.1f},{fps:.2f},{dm:.2f},{sm:.2f},"
                      f"{np.mean(frame_ms[-win_frames:]):.2f},"
                      f"{sys_pct:.1f},{self_pct:.1f},{voice_pct:.1f},{tc:.1f}\n")
            win_start, win_frames = c, 0

    total = time.perf_counter() - t0
    pf = bytes_out / max(1, frames)
    out = [
        "",
        f"=== {args.mode} @ {args.speed/1e6:.0f}MHz, cap={args.fps or 'uncapped'} ===",
        f"frames        {frames} in {total:.1f}s  ->  {frames/total:.1f} fps mean",
        f"fps by second min {min(fps_hist or [0]):.1f} / p50 {pct(fps_hist,0.5):.1f} / max {max(fps_hist or [0]):.1f}",
        f"frame ms      p50 {pct(frame_ms,0.5):.1f}  p95 {pct(frame_ms,0.95):.1f}  max {max(frame_ms or [0]):.1f}",
        f"  draw ms     p50 {pct(draw_ms,0.5):.1f}  p95 {pct(draw_ms,0.95):.1f}",
        f"  spi ms      p50 {pct(spi_ms,0.5):.1f}  p95 {pct(spi_ms,0.95):.1f}",
        f"bytes/frame   {pf:.0f}  ->  {pf*8/max(1e-9, pct(spi_ms,0.5))/1e3:.1f} Mbit/s on the wire",
        f"cpu system    mean {np.mean(sys_hist or [0]):.1f}%  max {max(sys_hist or [0]):.1f}%   (4 cores)",
        f"cpu this proc mean {np.mean(self_hist or [0]):.1f}%  max {max(self_hist or [0]):.1f}%   (of one core)",
        (f"cpu voice     mean {np.mean(voice_hist):.1f}%  max {max(voice_hist):.1f}%"
         if voice_hist else "cpu voice     not running"),
        f"missed deadlines {misses}/{frames}" if args.fps else "no fps cap",
        f"temp {temp_c():.1f}C   throttled {throttled()}",
    ]
    print("\n".join(out), flush=True)
    log.write("\n".join("# " + o for o in out) + "\n")
    log.close()

    panel.push(Image.new("RGB", (W, H), BG))
    panel.spi.close()
    panel.lgpio.gpiochip_close(panel.gpio)


def selftest():
    e = Eyes()
    t, dt = 0.0, 1 / 30.0
    seen_shut = seen_open = False
    for _ in range(2000):
        t += dt
        x, y, o = e.step(t, dt)
        assert -DX - 1 <= x <= DX + 1 and -DY - 1 <= y <= DY + 1, (x, y)
        assert 0.0 <= o <= 1.0, o
        seen_shut |= o < 0.25
        seen_open |= o > 0.99
    assert seen_shut and seen_open, "eyes must both blink shut and reopen"

    img = Image.new("RGB", (W, H), BG)
    dd = ImageDraw.Draw(img)
    for o in (0.05, 0.4, 1.0):                      # every blink phase must draw
        draw(dd, DX, DY, o)
    assert len(to565(img)) == W * H * 2
    x0, y0, x1, y1 = EYE_BOX[0]
    assert len(to565(img.crop((x0, y0, x1 + 1, y1 + 1)))) == (x1 - x0 + 1) * (y1 - y0 + 1) * 2
    # A pupil that wanders off the sclera reads as a floating dot, not an eye,
    # and a dirty box that clips it leaves smears behind on screen.
    assert DX + PR <= EW // 2 + 3, "pupil escapes the socket at full deflection"
    assert DY + PR <= EH // 2 + 3
    img.save("/tmp/eyes_preview.png")
    print("selftest ok — wrote /tmp/eyes_preview.png")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="full", choices=("full", "band", "dirty"))
    ap.add_argument("--speed", type=int, default=16000000)
    ap.add_argument("--secs", type=float, default=30)
    ap.add_argument("--fps", type=float, default=0, help="cap fps (0 = as fast as it goes)")
    ap.add_argument("--log", default="/tmp/eyes_test.csv")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--force", action="store_true", help="run even if desky is up (corrupts both SPI0 panels)")
    a = ap.parse_args()

    if a.selftest:
        selftest()
        sys.exit(0)

    # desky owns GPIO 24 and interleaves DC on this same bus. Two writers means
    # pixel data landing while the panel is in command mode — garbage on
    # screen1 as well, since it shares DC.
    if subprocess.run(["systemctl", "is-active", "--quiet", "desky"]).returncode == 0 and not a.force:
        sys.exit("desky.service is running. `sudo systemctl stop desky` first "
                 "(and `sudo systemctl start desky` after), or pass --force.")
    run(a)
