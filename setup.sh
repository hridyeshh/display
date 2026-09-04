#!/usr/bin/env bash
# One-command bootstrap for a fresh Raspberry Pi.
# Usage: sudo bash setup.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$REPO_DIR/.venv"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: sudo bash setup.sh"
  exit 1
fi

echo "[setup] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip python3-dev git swig liblgpio-dev

# NetworkManager owns wlan0 on Raspberry Pi OS and re-applies its own powersave
# default every time the link reconnects, so `iwconfig wlan0 power off` — and any
# systemd oneshot wrapping it — is reverted the first time the AP drops, with
# nothing logged. This conf.d file is the only form of the setting that persists.
echo "[setup] Disabling WiFi powersave..."
NM_CONF=/etc/NetworkManager/conf.d/wifi-powersave-off.conf
NM_WANT=$'[connection]\nwifi.powersave = 2'
if [ "$(cat "$NM_CONF" 2>/dev/null)" = "$NM_WANT" ]; then
  echo "[setup] $NM_CONF already correct"
else
  mkdir -p "$(dirname "$NM_CONF")"
  printf '%s\n' "$NM_WANT" > "$NM_CONF"
  # Restarted only when the file actually changed: a restart can drop the link
  # for a moment, and setup.sh is normally run over SSH.
  systemctl restart NetworkManager
  echo "[setup] wrote $NM_CONF and restarted NetworkManager"
fi

# Screens 1 and 2 hang off SPI0, screen 3 off SPI1, so both buses have to come
# up: spi0-2cs gives spidev0.0 and spidev0.1, spi1-1cs gives spidev1.0. Not
# spi0-3cs — that overlay does not exist on this kernel, and the firmware skips
# an unknown overlay silently, leaving screen 3 dark with no error anywhere.
BOOT_CFG=/boot/firmware/config.txt
REBOOT_REQUIRED=0
echo "[setup] Ensuring SPI overlays in $BOOT_CFG..."
if [ -f "$BOOT_CFG" ]; then
  MISSING=()
  for line in "dtparam=spi=on" "dtoverlay=spi0-2cs" "dtoverlay=spi1-1cs"; do
    grep -qE "^[[:space:]]*${line}([[:space:]]|\$)" "$BOOT_CFG" || MISSING+=("$line")
  done
  if [ ${#MISSING[@]} -eq 0 ]; then
    echo "[setup] SPI overlays already present"
  else
    # Appended under a fresh [all] header rather than edited in place:
    # config.txt is read in sections, and on a stock image the file ends inside
    # a model-specific one ([pi5]), where a bare append would never apply to
    # this board. A repeated [all] header is harmless.
    printf '\n[all]\n' >> "$BOOT_CFG"
    printf '%s\n' "${MISSING[@]}" >> "$BOOT_CFG"
    echo "[setup] added to $BOOT_CFG: ${MISSING[*]}"
    REBOOT_REQUIRED=1
  fi
else
  echo "[setup] WARNING: $BOOT_CFG not found — skipping SPI overlay setup"
fi

echo "[setup] Creating Python venv..."
if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$REPO_DIR/requirements.txt"
# Pinned and --no-deps: 0.6.0 requires tflite-runtime on Linux, which has no
# aarch64/py3.13 wheel, and the ONNX path never touches it. Its real deps are
# in requirements.txt above. The pin matters — 0.4.0 renames the constructor
# arg wake_word.py passes and defaults to that same missing backend.
"$VENV/bin/pip" install --quiet --no-deps openwakeword==0.6.0

# The wheel ships no weights. melspectrogram.onnx and embedding_model.onnx are
# the shared frontend every wake model runs through, including our own
# hey_desky.onnx, and without them Model() dies on NO_SUCHFILE. Idempotent —
# it skips whatever is already on disk. Before the chown so root-written files
# get handed back with the rest of the venv.
echo "[setup] Fetching openWakeWord feature models..."
"$VENV/bin/python" -c "import openwakeword.utils as u; u.download_models()"

# This script runs under sudo, so everything above is root-owned. Hand the venv
# back to whoever owns the repo, or the next `pip install` from a normal shell
# dies on EACCES halfway through unpacking a wheel.
chown -R "$(stat -c '%u:%g' "$REPO_DIR")" "$VENV"

echo "[setup] Checking fonts directory..."
if [ ! -d "$REPO_DIR/fonts" ] || [ -z "$(ls -A "$REPO_DIR/fonts" 2>/dev/null)" ]; then
  echo "[setup] WARNING: fonts/ directory is empty or missing. Add font files manually."
fi

echo "[setup] Making scripts executable..."
chmod +x "$REPO_DIR/deploy.sh" "$REPO_DIR/watch-deploy.sh"

# wake_word.py opens the mic and speaker by udev-pinned card *name*, so this
# rule is not optional decoration — without it both devices vanish at once and
# the voice service crash-loops on "Cannot get card index for mic". It was
# hand-written on the Pi once and installed by nothing, which is how a re-run of
# this script silently took the wake word and the speaker out together.
echo "[setup] Installing audio udev rules..."
cp "$REPO_DIR/85-desky-audio.rules" /etc/udev/rules.d/85-desky-audio.rules
chmod 644 /etc/udev/rules.d/85-desky-audio.rules
udevadm control --reload-rules
udevadm trigger --subsystem-match=sound --action=add

# The mic reads quiet at arm's length and openWakeWord was trained on
# normal-level speech, so a capture control left at its 0 default scores every
# utterance too low to fire. Analog gain before the ADC, not a multiplier after
# it — see the MIC_GAIN comment in voice/wake_word.py.
echo "[setup] Setting mic capture gain..."
if amixer -c mic sset Mic 16 >/dev/null 2>&1; then
  alsactl store || true
else
  echo "[setup] WARNING: no card named 'mic' yet — replug the USB hub and re-run"
fi

# Same knob on the way out. The adapter's playback control comes up attenuated
# on a fresh card, and it sits in front of every sound the Pi makes — the ring,
# the spoken answers, the reminders — so it is the one to top out before
# reaching for DESKY_VOLUME, which can only buy volume by clipping the peaks.
#
# The control's name varies between adapters, so set whichever of these the card
# actually has rather than guessing one and failing silently.
echo "[setup] Setting speaker playback volume..."
if amixer -c speaker scontrols >/dev/null 2>&1; then
  for ctl in PCM Speaker Master Headphone; do
    if amixer -c speaker sset "$ctl" 100% unmute >/dev/null 2>&1; then
      echo "[setup]   $ctl -> 100%"
    fi
  done
  alsactl store || true
else
  echo "[setup] WARNING: no card named 'speaker' yet — replug the USB hub and re-run"
fi

# Everything host-local lives here rather than inside a unit file, because the
# unit files get overwritten by the cp below on every re-run. DESKY_BULB_IP was
# hand-added to a unit once and a later setup.sh silently ate it — the bulb then
# refused every command with "the light isn't set up yet" and nothing in the
# repo showed why.
echo "[setup] Ensuring /etc/desky.env..."
if [ ! -f /etc/desky.env ]; then
  cat > /etc/desky.env <<'ENV'
# Host-local settings for the desky and desky-voice services.
# Edit, then: sudo systemctl restart desky desky-voice
#
# The WiZ bulb's address on this LAN. Without it every light command is refused,
# because a blank address would otherwise broadcast setPilot at the whole subnet.
# Give the bulb a DHCP reservation — it answers UDP at whatever address it has,
# and a lease that moves breaks this silently.
#DESKY_BULB_IP=192.168.0.5

# Extra playback gain, on top of the mixer this script already put at 100%.
# 1.0 is off. Raise it if the desk is still quiet — it clips the loud parts
# flat, which a ring tolerates and speech tolerates less. Past 2.5 it rasps.
#DESKY_VOLUME=1.5
ENV
  chmod 600 /etc/desky.env
  echo "[setup] wrote /etc/desky.env — set DESKY_BULB_IP in it"
else
  echo "[setup] /etc/desky.env already exists, leaving it alone"
fi

echo "[setup] Installing systemd services..."
cp "$REPO_DIR/desky.service" /etc/systemd/system/desky.service
cp "$REPO_DIR/desky-watch.service" /etc/systemd/system/desky-watch.service
cp "$REPO_DIR/desky-voice.service" /etc/systemd/system/desky-voice.service

systemctl daemon-reload
systemctl enable desky desky-watch desky-voice
systemctl start desky desky-watch desky-voice

echo "[setup] Done. Services running:"
systemctl --no-pager status desky desky-watch desky-voice || true

# `systemctl is-active` on its own is not enough to say a service is healthy:
# every unit here has Restart=always, so a service crash-looping on startup is
# reported active during each of its up windows. desky-voice did exactly that on
# a run where the udev trigger below had not finished re-registering the sound
# cards — nine green checks over a wake word that was dying every 25 seconds.
# Snapshot the restart counters now, let the services run, and compare after: a
# counter that moved across the window is a loop, whatever is-active says.
declare -A RESTARTS_BEFORE
for svc in desky desky-watch desky-voice; do
  RESTARTS_BEFORE[$svc]="$(systemctl show "$svc" -p NRestarts --value)"
done

# Long enough to span a restart cycle: the units back off ~5s and desky-voice
# needs ~18s to load the wake model before it can fail on the mic.
sleep 30

echo
echo "[setup] ===== Verification ====="
CHECKS_FAILED=0
check() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "  [ OK ] $label"
  else
    echo "  [FAIL] $label"
    CHECKS_FAILED=$((CHECKS_FAILED + 1))
  fi
}

for dev in /dev/spidev0.0 /dev/spidev0.1 /dev/spidev1.0; do
  check "$dev present" test -e "$dev"
done

# iwconfig lives in /usr/sbin, which is absent from a plain user PATH.
IWCONFIG="$(command -v iwconfig || echo /usr/sbin/iwconfig)"
powersave_off() { "$IWCONFIG" wlan0 2>/dev/null | grep -q "Power Management:off"; }
check "wlan0 WiFi powersave off" powersave_off

# Anything but 0x0 means the 5V rail has sagged at some point. The screens and
# the USB audio hub share it, so undervoltage surfaces as corrupt SPI frames and
# a mic that cuts out, never as an error that names power.
THROTTLED="$(vcgencmd get_throttled 2>/dev/null || echo unavailable)"
check "vcgencmd get_throttled is 0x0 (got: $THROTTLED)" test "$THROTTLED" = "throttled=0x0"

svc_healthy() {
  local svc="$1"
  systemctl is-active --quiet "$svc" || return 1
  [ "$(systemctl show "$svc" -p NRestarts --value)" = "${RESTARTS_BEFORE[$svc]}" ]
}
for svc in desky desky-watch desky-voice; do
  check "$svc service active and not restarting" svc_healthy "$svc"
done

# pip has died mid-install on pillow with an IncompleteRead more than once here.
# A venv short one package passes every check above and then crash-loops all
# three services on import, so confirm the imports themselves.
check "venv imports PIL, requests, spidev, numpy" \
  "$VENV/bin/python" -c "import PIL, requests, spidev, numpy"

echo
if [ "$REBOOT_REQUIRED" -eq 1 ]; then
  echo "[setup] *** REBOOT REQUIRED ***"
  echo "[setup] SPI overlays were added to $BOOT_CFG. Overlays load at boot only,"
  echo "[setup] so /dev/spidev* stays incomplete and the screens stay dark until:"
  echo "[setup]     sudo reboot"
elif [ "$CHECKS_FAILED" -eq 0 ]; then
  echo "[setup] All checks passed."
else
  echo "[setup] $CHECKS_FAILED check(s) FAILED — see above."
fi
