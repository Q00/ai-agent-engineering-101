#!/bin/sh
# Start the kit exactly as join.ps1 did on the laptop. A stop flag or a passed deadline ends the container without a restart loop.
set -eu
AGENT=/srv/market/repo/submissions/26620029/week-08
[ -e /srv/market/control/STOPPED ] && { echo "STOPPED flag set; not starting"; sleep infinity; }
MARKET_OWNER="$(head -n 1 "$AGENT/market_owner.txt" | tr -d '\r ')"
export MARKET_OWNER HOME=/tmp/kit
mkdir -p "$HOME"
cd /srv/market/repo
exec python -u scripts/market_kit.py join --dir "$AGENT" --market https://market.ourolabs.ai --name ddolcom --tunnel
