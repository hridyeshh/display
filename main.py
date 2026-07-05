"""
desky — Raspberry Pi display driver (3x ILI9341 240x320 over SPI)
"""

import os
import time
import math
import json
import threading
import socket
import random
import numpy as np
import spidev
import lgpio
import requests
from PIL import Image, ImageDraw, ImageFont

W, H = 240, 320
BACKEND = "https://web-production-12607.up.railway.app"
POLL_SEC = 5
HEARTBEAT_SEC = 5

BACKLIGHT_PIN = None
SPI_LOCK = threading.Lock()

import widgets.clock as widget_clock
import widgets.weather as widget_weather
import widgets.music as widget_music
import widgets.tasks as widget_tasks
import widgets.gif as widget_gif
import widgets.offline as widget_offline
import widgets.timer as widget_timer
import widgets.calendar as widget_calendar
import widgets.quote as widget_quote
import widgets.pet as widget_pet

WIDGETS = {
    "clock":   widget_clock.render,
    "weather": widget_weather.render,
    "music":   widget_music.render,
    "tasks":   widget_tasks.render,
    "gif":     widget_gif.render,
    "offline": widget_offline.render,
    "quote":   widget_quote.render,
    "pet":     widget_pet.render,
}

_IS_ONLINE = True
_KEY_NUM = {"screen1": 1, "screen2": 2, "screen3": 3}

CAL_SCREEN = "screen1"
CAL_MILESTONES = (30, 15)
BANNER_SEC = 10
MUSIC_IDLE_FALLBACK_SECONDS = 30  # 30 seconds of silence before fallback triggers

UPCOMING_EVENTS = []
_FIRED = set()

CONFIG = {"screen1": "clock", "screen2": "music", "screen3": "weather"}

GPIO: int = None  # type: ignore
_CLAIMED = set()

def _claim_output(pin, level):
    if pin is None: return
    assert GPIO is not None, "GPIO handle must be initialized"
    try:
        if pin not in _CLAIMED:
            lgpio.gpio_claim_output(GPIO, pin, level)
            _CLAIMED.add(pin)
        else:
            lgpio.gpio_write(GPIO, pin, level)
    except Exception as e:
        print(f"[warning] Pin {pin} busy or bypassed: {e}")

def render_placeholder(name):
    img = Image.new("RGB", (W, H), (10, 10, 10))
    d = ImageDraw.Draw(img)
    try:
        f_lbl = ImageFont.truetype("/home/hridyesh/display/fonts/PressStart2P-Regular.ttf", 10)
        f_sub = ImageFont.truetype("/home/hridyesh/display/fonts/PressStart2P-Regular.ttf", 8)
    except OSError:
        f_lbl = f_sub = ImageFont.load_default()
        
    d.rectangle([14, 14, W - 14, H - 14], outline=(34, 34, 34), width=2)
    
    t = time.time() * 4
    pulse = int(140 + 115 * math.sin(t))
    pulse = max(40, min(255, pulse))
    
    d.text((W // 2, H // 2 - 15), "INITIALIZING...", font=f_lbl, fill=(pulse, pulse, pulse), anchor="mm")
    d.text((W // 2, H // 2 + 15), f"WIDGET: {str(name).upper()}", font=f_sub, fill=(252, 60, 68), anchor="mm")
    return img

class DisplayPanel:
    def __init__(self, port, device, dc, rst, speed, flip_180=False):
        self.dc = dc
        self.rst = rst
        self.flip_180 = flip_180
        self.spi = spidev.SpiDev()
        self.spi.open(port, device)
        self.spi.max_speed_hz = speed
        self.spi.mode = 0
        _claim_output(dc, 0)
        if self.rst is not None:
            _claim_output(rst, 1)
        self._init()
        self.clear()

    def cmd(self, c):
        lgpio.gpio_write(GPIO, self.dc, 0)
        self.spi.writebytes([c])

    def dat(self, data):
        lgpio.gpio_write(GPIO, self.dc, 1)
        if isinstance(data, int): data = [data]
        self.spi.writebytes2(data)

    def _reset(self):
        if self.rst is None: return
        lgpio.gpio_write(GPIO, self.rst, 1)
        time.sleep(0.05)
        lgpio.gpio_write(GPIO, self.rst, 0)
        time.sleep(0.05)
        lgpio.gpio_write(GPIO, self.rst, 1)
        time.sleep(0.15)

    def clear(self):
        black = Image.new("RGB", (W, H), (0, 0, 0))
        self.show(black)

    def sleep(self):
        self.clear()
        with SPI_LOCK:
            self.cmd(0x28)
            self.cmd(0x10)
        time.sleep(0.12)

    def wake(self):
        with SPI_LOCK:
            self.cmd(0x11)
            time.sleep(0.12)
            self.cmd(0x29)
        time.sleep(0.02)

    def _init(self):
        self._reset()
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
        
        if self.flip_180:
            self.cmd(0x36); self.dat([0xE0])  
        else:
            self.cmd(0x36); self.dat([0x20])  
        
        self.cmd(0x3A); self.dat([0x55])
        self.cmd(0xB1); self.dat([0x00, 0x18])
        self.cmd(0xB6); self.dat([0x08, 0x82, 0x27])
        self.cmd(0xF2); self.dat([0x00])
        self.cmd(0x26); self.dat([0x01])
        self.cmd(0xE0); self.dat([0x0F, 0x31, 0x2B, 0x0C, 0x0E, 0x08, 0x4E, 0xF1, 0x37, 0x07, 0x10, 0x03, 0x0E, 0x09, 0x00])
        self.cmd(0xE1); self.dat([0x00, 0x0E, 0x14, 0x03, 0x11, 0x07, 0x31, 0xC1, 0x48, 0x08, 0x0F, 0x0C, 0x31, 0x36, 0x0F])
        self.cmd(0x11); time.sleep(0.12)
        self.cmd(0x29); time.sleep(0.02)

    def _set_window(self, x0, y0, x1, y1):
        self.cmd(0x2A); self.dat([x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF])
        self.cmd(0x2B); self.dat([y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF])
        self.cmd(0x2C)

    def show(self, img):
        if img.size != (W, H): img = img.resize((W, H), Image.Resampling.NEAREST)
        if img.mode != "RGB": img = img.convert("RGB")
        arr = np.asarray(img, dtype=np.uint16)
        r = (arr[:, :, 0] & 0xF8) << 8
        g = (arr[:, :, 1] & 0xFC) << 3
        b = (arr[:, :, 2]) >> 3
        rgb565 = (r | g | b).astype(">u2")
        with SPI_LOCK:
            self._set_window(0, 0, W - 1, H - 1)
            self.dat(rgb565.tobytes())

_GIF_KEY = {"screen1": "gif_url_1", "screen2": "gif_url_2", "screen3": "gif_url_3"}

def _apply_event_data(payload):
    payload = payload.strip()
    if not payload: return
    try: CONFIG.update(json.loads(payload))
    except Exception: pass

def _poll_config_once():
    try:
        r = requests.get(BACKEND.rstrip("/") + "/config", timeout=4)
        if r.status_code == 200: CONFIG.update(r.json())
    except Exception: pass

def fetch_config_loop():
    events_url = BACKEND.rstrip("/") + "/events"
    fails = 0
    while True:
        try:
            with requests.get(events_url, stream=True, timeout=(5, 60)) as resp:
                if resp.status_code != 200: raise RuntimeError(f"status {resp.status_code}")
                fails = 0
                for raw in resp.iter_lines(decode_unicode=True):
                    if not raw: continue          
                    if raw.startswith(":"): continue          
                    if raw.startswith("data:"): _apply_event_data(raw[len("data:"):])
        except Exception as e:
            fails += 1
        if fails >= 3:
            for _ in range(3):
                _poll_config_once()
                time.sleep(10)
            fails = 0
        else: time.sleep(3)

def connectivity_loop():
    global _IS_ONLINE
    while True:
        try:
            socket.setdefaulttimeout(3)
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("8.8.8.8", 53))
            s.close()
            _IS_ONLINE = True
        except Exception: _IS_ONLINE = False
        time.sleep(10)

def heartbeat_loop():
    url = BACKEND.rstrip("/") + "/api/heartbeat"
    while True:
        try: requests.post(url, timeout=3)
        except: pass
        time.sleep(HEARTBEAT_SEC)

def set_backlight(on):
    if BACKLIGHT_PIN is None: return
    try: _claim_output(BACKLIGHT_PIN, 1 if on else 0)
    except Exception as e: pass

def render_named(name, key):
    if name == "gif":
        gif_url = CONFIG.get(_GIF_KEY.get(key, ""), "")
        return widget_gif.render_url(gif_url) if gif_url else widget_gif.render()
    render_func = WIDGETS.get(name)
    return render_func() if render_func else render_placeholder(name)

def _calendar_trigger(now):
    for ev in list(UPCOMING_EVENTS):
        uid = ev.get("uid")
        start = ev.get("start", 0)
        for m in CAL_MILESTONES:
            trigger = start - m * 60
            if trigger <= now < trigger + BANNER_SEC and (uid, m) not in _FIRED:
                _FIRED.add((uid, m))
                return (ev.get("title", "Event"), ev.get("when", ""), m)
    return None

def calendar_loop():
    global UPCOMING_EVENTS
    url = os.environ.get("CALENDAR_ICS_URL", "").strip()
    if not url: return
    try:
        import icalendar
        import recurring_ical_events
    except Exception as e: return
    from datetime import datetime, timedelta, timezone

    while True:
        try:
            raw = requests.get(url, timeout=8).content
            cal = icalendar.Calendar.from_ical(raw)
            now_dt = datetime.now(timezone.utc)
            occurrences = recurring_ical_events.of(cal).between(
                now_dt - timedelta(minutes=1), now_dt + timedelta(hours=2)
            )
            parsed = []
            for e in occurrences:
                dt = e.get("DTSTART")
                if dt is None: continue
                start = dt.dt
                if not isinstance(start, datetime): continue
                if start.tzinfo is None: start = start.replace(tzinfo=timezone.utc)
                start_ts = start.timestamp()
                title = str(e.get("SUMMARY", "Event"))
                uid = f"{e.get('UID', '')}-{int(start_ts)}"
                try: when = start.astimezone().strftime("%-I:%M %p")
                except Exception: when = ""
                parsed.append({"uid": uid, "title": title, "start": start_ts, "when": when})
            UPCOMING_EVENTS = parsed
            if len(_FIRED) > 200: _FIRED.clear()
        except Exception as ex: pass
        time.sleep(60)

def screen_loop(panel, key):
    sleeping = False
    is_cal_screen = (key == CAL_SCREEN)
    banner_until = 0.0
    banner_payload = None
    _last_end = 0.0      
    _timer_start = 0.0
    screen_num = _KEY_NUM.get(key, 1)

    while True:
        power = str(CONFIG.get("power_state", "ON")).upper()
        if power == "OFF":
            if not sleeping:
                try:
                    panel.sleep()
                    set_backlight(False)
                except Exception: pass
                sleeping = True
            time.sleep(0.5)
            continue

        if sleeping:
            try:
                panel.wake()
                set_backlight(True)
            except Exception: pass
            sleeping = False

        if not _IS_ONLINE:
            try: panel.show(widget_offline.render())
            except Exception: pass
            time.sleep(1.0)
            continue

        now = time.time()
        
        # --- CALENDAR BANNER LOGIC ---
        if is_cal_screen:
            trig = _calendar_trigger(now)
            if trig:
                banner_payload = trig
                banner_until = now + BANNER_SEC
            if banner_payload and now < banner_until:
                try: panel.show(widget_calendar.render_banner(*banner_payload))
                except Exception: pass
                time.sleep(0.3)
                continue

        name = CONFIG.get(key, "")

        # --- NEW: STATE CHANGE DETECTOR ---
        # If the backend changed the widget (e.g. Timer -> Music), wipe the fallback variables clean
        if not hasattr(panel, "current_config"):
            panel.current_config = name
            panel.last_playing = now
            panel.in_fallback = False
            panel.fallback_widget = "quote"
            
        if panel.current_config != name:
            panel.current_config = name
            panel.last_playing = now
            panel.in_fallback = False
        
        # --- TIMER LOGIC ---
        if name == "timer":
            n = screen_num
            try: end = float(CONFIG.get(f"timer_end_{n}", 0) or 0)
            except (TypeError, ValueError): end = 0.0
            if end != _last_end:
                _timer_start = now
                _last_end = end
            total_sec = max(1, end - _timer_start) if end > 0 else None
            try:
                if end <= 0: img = widget_timer.render(0, None)
                elif now < end: img = widget_timer.render(int(round(end - now)), total_sec)
                elif now < end + BANNER_SEC: img = widget_timer.render_done(int(now * 2) % 2 == 0)
                else:
                    prev = str(CONFIG.get(f"prev_{n}", "clock") or "clock")
                    if prev == "timer": prev = "clock"
                    img = render_named(prev, key)
                img = apply_encoder_modifications(img, screen_num)
                panel.show(img)
            except Exception: pass
            time.sleep(0.25)
            continue

        # --- MUSIC AUTO-FALLBACK LOGIC ---
        if name == "music":
            music_data = widget_music._fetch_spotify() or {}
            status = music_data.get("status", "not_configured")

            if status == "playing":
                panel.last_playing = now
                panel.in_fallback = False
            elif (now - panel.last_playing) > MUSIC_IDLE_FALLBACK_SECONDS:
                if not panel.in_fallback:
                    panel.fallback_widget = random.choice(["quote", "pet"])
                    panel.in_fallback = True
                name = panel.fallback_widget
            else:
                panel.in_fallback = False

        try:
            img = render_named(name, key)
            img = apply_encoder_modifications(img, screen_num)
            panel.show(img)
        except Exception: time.sleep(1.0)
        time.sleep(0.05 if name == "gif" else 1.0)

def apply_encoder_modifications(img, screen_num):
    img = img.copy()
    if img.mode != "RGB": img = img.convert("RGB")
    focused_target = 0
    try:
        focus_file = "/dev/shm/desky_focus"
        with open(focus_file, "r") as f: focused_target = int(f.read().strip())
        if time.time() - os.path.getmtime(focus_file) > 2.5: focused_target = 0
    except Exception: pass
    
    brightness_val = 100
    try:
        with open(f"/dev/shm/desky_bright_s{screen_num}", "r") as f: brightness_val = int(f.read().strip())
    except Exception: pass
    
    if brightness_val < 100:
        factor = max(0.1, brightness_val / 100.0)
        img = Image.eval(img, lambda pixel: int(pixel * factor))
        
    if focused_target == screen_num:
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, W - 1, H - 1], outline=(252, 60, 68), width=3)
    return img
    
def main():
    global GPIO
    GPIO = lgpio.gpiochip_open(0)
    try:
        for pin in (25, 23):
            lgpio.gpio_claim_output(GPIO, pin, 1)
            lgpio.gpio_write(GPIO, pin, 0)
        time.sleep(0.1)
        for pin in (25, 23): lgpio.gpio_write(GPIO, pin, 1)
        time.sleep(0.1)
    except: pass

    panels = [
        ("screen1", DisplayPanel(port=0, device=0, dc=24, rst=25, speed=16000000, flip_180=True)),
        ("screen2", DisplayPanel(port=0, device=1, dc=24, rst=None, speed=16000000, flip_180=True)),
        ("screen3", DisplayPanel(port=1, device=0, dc=22, rst=23, speed=8000000, flip_180=True)),
    ]
    
    set_backlight(True)
    threads = [
        threading.Thread(target=fetch_config_loop, daemon=True),
        threading.Thread(target=heartbeat_loop, daemon=True),
        threading.Thread(target=connectivity_loop, daemon=True),
        threading.Thread(target=calendar_loop, daemon=True),
    ]
    for key, panel in panels: threads.append(threading.Thread(target=screen_loop, args=(panel, key), daemon=True))
    for t in threads: t.start()
    
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt: pass
    finally:
        for _, panel in panels:
            try: panel.spi.close()
            except: pass
        if GPIO is not None: lgpio.gpiochip_close(GPIO)

if __name__ == "__main__": main()