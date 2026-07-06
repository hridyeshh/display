import os
import time
import lgpio

# --- CONFIGURE YOUR GPIO PINS HERE ---
PIN_A = 17
PIN_B = 27
PIN_BTN = 26

current_screen = 1
bright = {1: 100, 2: 100, 3: 100}

def write_focus():
    try:
        with open("/dev/shm/desky_focus", "w") as f:
            f.write(str(current_screen))
    except Exception: pass

def write_bright(screen, val):
    try:
        with open(f"/dev/shm/desky_bright_s{screen}", "w") as f:
            f.write(str(val))
    except Exception: pass

# Initialize shared memory files in RAM
write_focus()
for s in [1, 2, 3]:
    write_bright(s, bright[s])

h = lgpio.gpiochip_open(0)

# Configure hardware pull-ups and alerts using correct lgpio constants
lgpio.gpio_claim_alert(h, PIN_A, lgpio.BOTH_EDGES, lgpio.SET_PULL_UP)
lgpio.gpio_claim_alert(h, PIN_B, lgpio.BOTH_EDGES, lgpio.SET_PULL_UP)
lgpio.gpio_claim_alert(h, PIN_BTN, lgpio.FALLING_EDGE, lgpio.SET_PULL_UP)

last_a = lgpio.gpio_read(h, PIN_A)
last_btn_ns = 0

def cbf(chip, gpio, level, timestamp):
    global current_screen, last_a, last_btn_ns

    # Handle Button Clicks (debounce ~250ms; timestamp is in nanoseconds)
    if gpio == PIN_BTN and level == 0:
        if timestamp - last_btn_ns < 250_000_000:
            return
        last_btn_ns = timestamp
        current_screen += 1
        if current_screen > 3: current_screen = 1
        write_focus()
        return

    # Handle Rotation
    if gpio == PIN_A:
        a = level
        if a == 0 and last_a == 1:
            b = lgpio.gpio_read(h, PIN_B)
            if b == 1:
                bright[current_screen] = min(100, bright[current_screen] + 5)
            else:
                bright[current_screen] = max(5, bright[current_screen] - 5)

            write_bright(current_screen, bright[current_screen])
            write_focus()
        last_a = a

# Register callbacks
cb_a = lgpio.callback(h, PIN_A, lgpio.BOTH_EDGES, cbf)
cb_btn = lgpio.callback(h, PIN_BTN, lgpio.FALLING_EDGE, cbf)

try:
    while True: time.sleep(3600)
except KeyboardInterrupt:
    pass
finally:
    lgpio.gpiochip_close(h)