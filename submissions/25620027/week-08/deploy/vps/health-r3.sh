#!/usr/bin/env bash
# Cron, every 3 minutes: while a launch is active (marker r3-active, set by launch-r3.sh
# and switch-r3.sh, cleared by stop-r3.sh) and the round has not ended, relaunch the
# trading container if it is not running. The kit re-joins with the same keys, so a
# restart keeps the shop and buyer identity. First seen need: 2026-11-01 09:00 KST,
# when the kit's market join timed out behind a just-recreated tunnel and exited.
set -uo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
BASE=/docker/ax-week08-25620027
# The release to relaunch is whatever `current` points at; the cron argument is only
# a fallback, so installing a new release never needs a cron edit.
RELEASE="$(basename "$(readlink -f "$BASE/current" 2>/dev/null || echo "${1:-a6a64bb10282}")")"
LOG="$BASE/health-r3.log"
END_UTC="2026-11-17T14:59:59Z"   # 2026-11-17 23:59:59 KST
[ -f "$BASE/r3-active" ] || exit 0
if [ "$(date -u +%FT%TZ)" \> "$END_UTC" ]; then
  echo "$(date -u +%FT%TZ) round ended; marker removed" >>"$LOG"
  rm -f "$BASE/r3-active"
  exit 0
fi
C=ax-week08-25620027-r3-r3-1
# The manager runs beside the kit inside the container. `docker top` sometimes fails
# with a daemon error and returned nothing, which logged 31 false "missing" relaunches
# on 2026-11-01..02 while the manager was fine. Read /proc inside the container
# instead (the bracket keeps this command from matching itself), twice.
manager_running() {
  docker exec "$C" sh -c 'grep -lsa "r3[.]manager" /proc/[0-9]*/cmdline' >/dev/null 2>&1
}
if docker ps --format '{{.Names}}' | grep -qx "$C"; then
  manager_running && exit 0
  started=$(docker inspect --format '{{.State.StartedAt}}' "$C" 2>/dev/null)
  age=$(( $(date -u +%s) - $(date -u -d "$started" +%s 2>/dev/null || echo 0) ))
  [ "$age" -lt 180 ] && exit 0   # just started; the manager may not be up yet
  sleep 20
  manager_running && exit 0
  # A running container is left alone by `compose up -d`, so restart it in place
  # (same image, same tunnel) to bring the manager back.
  echo "== $(date -u +%FT%TZ) manager process missing twice (container age ${age}s); restarting container" >>"$LOG"
  docker restart -t 20 "$C" >>"$LOG" 2>&1
  exit 0
fi
echo "== $(date -u +%FT%TZ) trading container not running; relaunching $RELEASE" >>"$LOG"
docker ps -a --format '{{.Names}} {{.Status}}' | grep ax-week08 >>"$LOG" 2>&1 || true
docker logs --tail 20 ax-week08-25620027-r3-r3-1 >>"$LOG" 2>&1 || true
"$BASE/launch-r3.sh" "$RELEASE" >>"$LOG" 2>&1
