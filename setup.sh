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
apt-get install -y -qq python3 python3-venv python3-pip git

echo "[setup] Creating Python venv..."
if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$REPO_DIR/requirements.txt"

echo "[setup] Checking fonts directory..."
if [ ! -d "$REPO_DIR/fonts" ] || [ -z "$(ls -A "$REPO_DIR/fonts" 2>/dev/null)" ]; then
  echo "[setup] WARNING: fonts/ directory is empty or missing. Add font files manually."
fi

echo "[setup] Making scripts executable..."
chmod +x "$REPO_DIR/deploy.sh" "$REPO_DIR/watch-deploy.sh"

echo "[setup] Installing systemd services..."
cp "$REPO_DIR/desky.service" /etc/systemd/system/desky.service
cp "$REPO_DIR/desky-watch.service" /etc/systemd/system/desky-watch.service

systemctl daemon-reload
systemctl enable desky desky-watch
systemctl start desky desky-watch

echo "[setup] Done. Services running:"
systemctl --no-pager status desky desky-watch || true
