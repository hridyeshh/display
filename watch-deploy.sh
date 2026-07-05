#!/usr/bin/env bash
# Poll origin/main every 60s; run deploy.sh whenever new commits appear.
# Runs forever under the desky-watch systemd service.
set -uo pipefail

REPO=/home/hridyesh/display
BRANCH=main
cd "$REPO" || { echo "[watch] cannot cd $REPO"; exit 1; }

echo "[watch] started, polling origin/$BRANCH every 60s"
while true; do
  if git fetch --quiet origin "$BRANCH"; then
    LOCAL=$(git rev-parse HEAD)
    REMOTE=$(git rev-parse "origin/$BRANCH")
    if [ "$LOCAL" != "$REMOTE" ]; then
      echo "[watch] $LOCAL -> $REMOTE, deploying"
      ./deploy.sh || echo "[watch] deploy failed, will retry next cycle"
    fi
  else
    echo "[watch] fetch failed, retrying next cycle"
  fi
  sleep 60
done
