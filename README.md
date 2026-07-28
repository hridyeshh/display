# Desky

Raspberry Pi display driver — drives 3x ILI9341 240x320 screens over SPI.

## Fresh Pi Setup

After flashing your Pi and connecting via SSH:

```bash
git clone <your-repo-url> /home/hridyesh/display
cd /home/hridyesh/display
sudo bash setup.sh
```

That's it. `setup.sh` handles everything:
- Installs Python 3 and system dependencies
- Creates a virtualenv and installs pip packages from `requirements.txt`
- Copies `desky.service` and `desky-watch.service` into systemd
- Enables and starts both services

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
requirements.txt     # Python dependencies
```
