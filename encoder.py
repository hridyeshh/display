import os
import time
import lgpio

# --- CONFIGURE YOUR GPIO PINS HERE ---
PIN_A = 17
PIN_B = 27
PIN_BTN = 26

current_screen = 1
bright = {1: 100, 2: 100, 3: 100}

# While an alarm rings the encoder stops being a brightness knob: turn snoozes,
# press dismisses. Hijacking it beats wiring a second input for something that
# happens once a day, and losing brightness control for those few minutes is the
# right trade — nobody is dimming a screen while an alarm goes off.
#
# main.py raises the flag and acts on the command; this file only writes it.
ALARM_FILE = "/dev/shm/desky_alarm"
ALARM_CMD_FILE = "/dev/shm/desky_alarm_cmd"

def write_alarm_cmd(cmd):
    try:
        with open(ALARM_CMD_FILE, "w") as f:
            f.write(cmd)
    except Exception: pass

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

    # An alarm outranks the knob. Checked first and returned from, so none of the
    # brightness or focus handling below runs while one is ringing.
    if os.path.exists(ALARM_FILE):
        if gpio == PIN_BTN and level == 0:
            if timestamp - last_btn_ns < 250_000_000:
                return
            last_btn_ns = timestamp
            write_alarm_cmd("dismiss")
        elif gpio == PIN_A and level == 0:
            # Direction is deliberately ignored: half the detents snoozing and
            # half doing nothing is not what a hand reaching for a ringing alarm
            # in the dark wants. Any turn snoozes.
            #
            # last_a still has to be maintained, or the rotation state machine
            # below resumes mid-detent once the alarm stops.
            last_a = level
            write_alarm_cmd("snooze")
        return

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