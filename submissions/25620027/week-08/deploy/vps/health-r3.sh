#!/usr/bin/env bash
# Cron, every 10 minutes: while the Round 3 run is active (marker r3-active) and the
# round has not ended, relaunch the trading container if it is not running. The kit
# re-joins with the same keys, so a restart keeps the shop and buyer identity.
set -uo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
BASE=/docker/ax-week08-25620027
RELEASE="${1:-a6a64bb10282}"
LOG="$BASE/health-r3.log"
END_UTC="2026-11-17T14:59:59Z"   # 2026-11-17 23:59:59 KST
[ -f "$BASE/r3-active" ] || exit 0
if [ "$(date -u +%FT%TZ)" \> "$END_UTC" ]; then
  echo "$(date -u +%FT%TZ) round ended; marker removed" >>"$LOG"
  rm -f "$BASE/r3-active"
  exit 0
fi
if docker ps --format '{{.Names}}' | grep -qx 'ax-week08-25620027-r3-r3-1'; then
  # the policy manager runs beside the kit; if it died, restart the whole release
  if docker top ax-week08-25620027-r3-r3-1 2>/dev/null | grep -q 'r3.manager'; then
    exit 0
  fi
  started=$(docker inspect --format '{{.State.StartedAt}}' ax-week08-25620027-r3-r3-1 2>/dev/null)
  age=$(( $(date -u +%s) - $(date -u -d "$started" +%s 2>/dev/null || echo 0) ))
  [ "$age" -lt 180 ] && exit 0   # just started; the manager may not be up yet
  echo "== $(date -u +%FT%TZ) manager process missing (container age ${age}s); relaunching $RELEASE" >>"$LOG"
  "$BASE/launch-r3.sh" "$RELEASE" >>"$LOG" 2>&1
  exit 0
fi
echo "== $(date -u +%FT%TZ) trading container not running; relaunching $RELEASE" >>"$LOG"
docker ps -a --format '{{.Names}} {{.Status}}' | grep ax-week08 >>"$LOG" 2>&1 || true
docker logs --tail 20 ax-week08-25620027-r3-r3-1 >>"$LOG" 2>&1 || true
"$BASE/launch-r3.sh" "$RELEASE" >>"$LOG" 2>&1
