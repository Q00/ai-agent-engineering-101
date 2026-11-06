#!/bin/sh
# Apply hook-side code to the live data folder without recreating the container (the Quick Tunnel URL
# changes on every container start, and other agents remember this shop by URL).
#
#   docker/hot_update.sh            copy, then restart the strategist loop inside the container
#   docker/hot_update.sh --dry-run  show what would change
#
# Safe to copy live: the kit starts market_buyer.py, restock.py and review.py as a new process on every run,
# so they and the modules they import (policy.py, tools/, memory/) take effect at their next run; the
# strategist loop is restarted here (it imports strategist/ once; no tunnel is involved).
# NOT copied: seller.py (a long-lived A2A server: needs a container recreate, batch such changes),
# market_kit.py and market_runtime.py (the kit and the tunnel), strategy/ (the live, tuned SKILL.md),
# docker/ (image-time only), tests. The image is rebuilt at the next real recreate, so /data and the image
# only drift until then.
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd)
DATA=${MARKET_DATA_DIR:-/Users/psyoung82/docker/compose/term/data}
COMPOSE="podman compose -f $HERE/compose.yaml --project-directory $HERE"
DRY=""
[ "${1:-}" = "--dry-run" ] && DRY="--dry-run"

rsync -ai --checksum $DRY \
  --include='/market_buyer.py' --include='/restock.py' --include='/review.py' --include='/policy.py' \
  --include='/tools/' --include='/tools/*.py' \
  --include='/memory/' --include='/memory/*.py' \
  --include='/strategist/' --include='/strategist/*.py' \
  --exclude='*' \
  "$HERE/" "$DATA/" | grep -E '^>f' | sed 's/^>f[^ ]* /updated: /' || true

if [ -z "$DRY" ]; then
  for f in market_buyer.py restock.py review.py policy.py; do python3 -m py_compile "$DATA/$f"; done
  # the loop's pid is in its heartbeat (the slim image has no procps); a new loop then starts detached
  $COMPOSE exec -d market as-agent sh -c 'pid=$(python -c "import json;print(json.load(open(\"/data/strategist-home/heartbeat.json\"))[\"pid\"])" 2>/dev/null); [ -n "$pid" ] && kill "$pid" 2>/dev/null; sleep 2; mkdir -p /data/strategist-home && exec python -m strategist >> /data/strategist-home/console.log 2>&1' >/dev/null 2>&1 || true
  sleep 65                                        # the new loop writes its first heartbeat after HEARTBEAT_S
  $COMPOSE exec market as-agent python -m strategist health >/dev/null 2>&1 && echo "strategist loop: restarted, healthy" || echo "strategist loop: health not fresh yet (heartbeat is every 60 s)"
fi
