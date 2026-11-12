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
LIST=$(mktemp)
[ "${1:-}" = "--dry-run" ] && DRY="--dry-run" && USER_DRY=1

# A policy key (RANGES) the running seller's policy.py does not know makes it reject SKILL.md as soon as the
# strategist writes the key (11-11: 7 h on DEFAULTS over a value, and an unknown key is the same error). The
# seller loaded the image's policy.py (launch.py copies the image over /data at every start), so the repo's keys
# are compared with the image's: new keys -> nothing is copied and only the strategist loop is restarted, until
# the container is recreated from a rebuilt image (the user's command: podman compose up -d).
IMG_KEYS=$($COMPOSE exec -T market python -c "import sys; sys.path.insert(0, '/opt/agent'); import policy; print(','.join(sorted(policy.RANGES)))" 2>/dev/null | tr -d '\r' | tail -1 || true)
REPO_KEYS=$(python3 -c "import sys; sys.path.insert(0, '$HERE'); import policy; print(','.join(sorted(policy.RANGES)))")
NEW_KEYS=$(python3 -c "import sys; a, b = set(sys.argv[1].split(',')), set(sys.argv[2].split(',')); print(' '.join(sorted(b - a)))" "$IMG_KEYS" "$REPO_KEYS")
if [ -n "$IMG_KEYS" ] && [ -n "$NEW_KEYS" ]; then
  echo "SKIPPED copy: policy.py adds keys the running seller's policy.py lacks ($NEW_KEYS);"
  echo "         rebuild the image and recreate the container (podman compose up -d) first"
  DRY="--dry-run"
  SKIPPED=1
fi
[ -z "$IMG_KEYS" ] && echo "note: could not read the image's policy.py RANGES (container down?); copying anyway"

rsync -ai --checksum $DRY \
  --include='/market_buyer.py' --include='/restock.py' --include='/review.py' --include='/policy.py' \
  --include='/tools/' --include='/tools/*.py' \
  --include='/memory/' --include='/memory/*.py' \
  --include='/strategist/' --include='/strategist/*.py' \
  --exclude='*' \
  "$HERE/" "$DATA/" | grep -E '^>f' | sed 's/^>f[^ ]* /updated: /' | tee "$LIST" || true
# The seller imports policy.py once, at its start: a RANGES change here is not seen by the running seller, and a
# SKILL.md value the strategist (on the new RANGES) writes outside the old ones makes the seller fall back to
# DEFAULTS (11-11: a 23M warranty cap over the old 20M range, 7 h on DEFAULTS). Recreate the container then.
if grep -q '^updated: policy.py$' "$LIST"; then
  echo "WARNING: policy.py changed. The running seller keeps the policy.py it loaded at start; if RANGES widened,"
  echo "         recreate the container (podman compose up -d) before any SKILL.md value uses the wider range."
fi
rm -f "$LIST"

if [ -z "${USER_DRY:-}" ]; then
  [ -z "$DRY" ] && for f in market_buyer.py restock.py review.py policy.py; do python3 -m py_compile "$DATA/$f"; done
  # the loop's pid is in its heartbeat (the slim image has no procps); a new loop then starts detached
  T0=$(date +%s)
  $COMPOSE exec -d market as-agent sh -c 'pid=$(python -c "import json;print(json.load(open(\"/data/strategist-home/heartbeat.json\"))[\"pid\"])" 2>/dev/null); [ -n "$pid" ] && kill "$pid" 2>/dev/null; sleep 2; mkdir -p /data/strategist-home && STRATEGIST_SPRINT=${STRATEGIST_SPRINT:-1} exec python -m strategist >> /data/strategist-home/console.log 2>&1' >/dev/null 2>&1 || true
  # STRATEGIST_SPRINT=1 (the end game, from 11-12 22:00): 5 keys a run, a 30 min cooldown, x3 steps, the model asked
  # during outcome waits (strategist/run.Config.sprint). STRATEGIST_SPRINT=0 sh docker/hot_update.sh turns it off.
  sleep 65                                        # the new loop writes its first heartbeat after HEARTBEAT_S
  # only a heartbeat written after this restart counts: `python -m strategist health` also accepts a fresh
  # state.json, which a run minutes earlier leaves behind, and on 11-12 02:32 that said "healthy" for a loop
  # whose exec had failed in a container still starting up
  if python3 -c "import json,sys; sys.exit(0 if json.load(open('$DATA/strategist-home/heartbeat.json'))['ts'] >= $T0 else 1)" 2>/dev/null; then
    echo "strategist loop: restarted, healthy"
  else
    echo "strategist loop: NOT running (no heartbeat since the restart); run this script again"
    exit 1
  fi
fi
