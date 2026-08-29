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

echo "[setup] Installing systemd services..."
cp "$REPO_DIR/desky.service" /etc/systemd/system/desky.service
cp "$REPO_DIR/desky-watch.service" /etc/systemd/system/desky-watch.service
cp "$REPO_DIR/desky-voice.service" /etc/systemd/system/desky-voice.service

systemctl daemon-reload
systemctl enable desky desky-watch desky-voice
systemctl start desky desky-watch desky-voice

echo "[setup] Done. Services running:"
systemctl --no-pager status desky desky-watch desky-voice || true
