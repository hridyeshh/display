#!/usr/bin/env bash
# Sync ~/display to origin/main and restart the desky service.
#
# Uses `reset --hard` (not `pull`) so regenerated *.pyc or any local drift on
# the Pi can never block the update. `reset --hard` only touches TRACKED files —
# untracked local-only files (.venv/, media/) are left alone.
set -uo pipefail

REPO=/home/hridyesh/display
BRANCH=main
cd "$REPO" || { echo "[deploy] cannot cd $REPO"; exit 1; }

git fetch --quiet origin "$BRANCH" || { echo "[deploy] fetch failed"; exit 1; }
git reset --hard "origin/$BRANCH" || { echo "[deploy] reset failed"; exit 1; }

sudo systemctl restart desky
echo "[deploy] synced to $(git rev-parse --short HEAD) and restarted desky at $(date -Is)"
