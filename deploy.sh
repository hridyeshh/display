#!/usr/bin/env bash
# Sync ~/display to origin/main and restart the desky services.
#
# Uses `reset --hard` (not `pull`) so regenerated *.pyc or any local drift on
# the Pi can never block the update. `reset --hard` only touches TRACKED files —
# untracked local-only files (.venv/, media/) are left alone.
#
# Everything runs inside { } because `reset --hard` rewrites this very file
# mid-run. Bash reads a script lazily by byte offset, so without the braces a
# deploy that changes deploy.sh's own length makes bash resume at the wrong
# offset and run garbage. The braces force it to parse the whole body first.
set -uo pipefail
{

REPO=/home/hridyesh/display
BRANCH=main
cd "$REPO" || { echo "[deploy] cannot cd $REPO"; exit 1; }

git fetch --quiet origin "$BRANCH" || { echo "[deploy] fetch failed"; exit 1; }
git reset --hard "origin/$BRANCH" || { echo "[deploy] reset failed"; exit 1; }

# desky-voice too: it runs wake_word.py out of this same repo, so a deploy that
# restarts only the display leaves the wake word on whatever code was live at
# the last reboot.
sudo systemctl restart desky desky-voice
echo "[deploy] synced to $(git rev-parse --short HEAD) and restarted desky, desky-voice at $(date -Is)"

}
