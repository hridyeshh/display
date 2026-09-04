# Desky

Raspberry Pi display driver — drives 3x ILI9341 240x320 screens over SPI.

## Fresh Pi Setup

After flashing your Pi and connecting via SSH:

```bash
git clone <your-repo-url> /home/hridyesh/display
cd /home/hridyesh/display
sudo bash setup.sh
```

That's it. `setup.sh` handles everything, and every step is idempotent — re-running
it is safe:
- Installs Python 3 and system dependencies
- Disables WiFi powersave (see [WiFi powersave](#wifi-powersave) below)
- Enables the SPI overlays the three screens need (see [SPI overlays](#spi-overlays))
- Creates a virtualenv and installs pip packages from `requirements.txt`
- Installs the audio udev rules and sets mic/speaker levels
- Copies `desky.service`, `desky-watch.service` and `desky-voice.service` into systemd
- Enables and starts all three services
- Prints a verification summary (SPI devices, powersave, undervoltage, services,
  venv imports)

**If setup.sh says a reboot is required, reboot.** It only says so when it had to
add SPI overlays, and those load at boot only — until then the screens stay dark.
Re-run `sudo bash setup.sh` afterwards to see a clean verification summary.

## Hardware Notes

Two settings are not defaults and cost real debugging time when they are missing.
`setup.sh` applies both; they are documented here because the failure modes are
silent and point somewhere else.

### SPI overlays

Screens 1 and 2 share SPI0, screen 3 is on SPI1. That needs three device nodes —
`/dev/spidev0.0`, `/dev/spidev0.1`, `/dev/spidev1.0` — which means both buses have
to be brought up in `/boot/firmware/config.txt`:

```
dtparam=spi=on

[all]
dtoverlay=spi0-2cs
dtoverlay=spi1-1cs
```

**Do not use `dtoverlay=spi0-3cs`.** That overlay does not exist on this kernel.
The firmware skips an unknown overlay without logging anything, so the symptom is
screen 3 simply never lighting up — no error in `dmesg`, nothing in the service
logs, just a missing `/dev/spidev1.0`.

Overlays are read at boot. Editing `config.txt` changes nothing until you reboot.

### WiFi powersave

Raspberry Pi OS manages `wlan0` through NetworkManager, which re-applies its own
powersave setting every time the link reconnects. So `iwconfig wlan0 power off`
works right up until the first reconnect, and a systemd oneshot wrapping that same
command is no better — it runs once at boot and is quietly undone later. The
symptom is the Pi becoming slow or unreachable over SSH after idling, then being
fine again once you touch it.

The only form of the setting that persists is a NetworkManager config file:

```
# /etc/NetworkManager/conf.d/wifi-powersave-off.conf
[connection]
wifi.powersave = 2
```

(`2` means disabled; `3` is enabled.) It takes effect on the next NetworkManager
restart or reconnect. Check it with:

```bash
/usr/sbin/iwconfig wlan0 | grep "Power Management"   # want: Power Management:off
```

`iwconfig` lives in `/usr/sbin`, which is not on the PATH for a non-interactive
`ssh pi "..."` command — hence the full path.

### Undervoltage

The screens and the USB audio hub share the 5V rail. When it sags, the failure
looks like corrupt SPI frames and a mic that cuts out, never like a power error:

```bash
vcgencmd get_throttled   # want: throttled=0x0
```

Anything else means the supply has browned out at some point.

## Services

| Service | Purpose |
|---------|---------|
| `desky` | Runs `main.py` — the display driver |
| `desky-watch` | Polls `origin/main` every 60s, auto-deploys on new commits |

### Manual control

```bash
sudo systemctl restart desky          # restart display
sudo systemctl stop desky-watch       # pause auto-updates
journalctl -u desky -f                # tail logs
```

## Auto-Updates

Push to `main` and the Pi picks it up within 60 seconds. The watcher runs `deploy.sh`, which does a hard reset to `origin/main` and restarts the desky and desky-voice services.

## Project Structure

```
main.py              # display driver entry point
widgets/             # per-screen widget modules
fonts/               # font files (tracked in git)
encoder.py           # rotary encoder input
deploy.sh            # pull + restart (called by watcher)
watch-deploy.sh      # polling loop for auto-deploy
desky.service        # systemd unit for the display driver
desky-watch.service  # systemd unit for the auto-updater
setup.sh             # one-command Pi bootstrap
85-desky-audio.rules # udev rules pinning the mic and speaker card names
requirements.txt     # Python dependencies
```
