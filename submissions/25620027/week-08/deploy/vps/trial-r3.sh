#!/usr/bin/env bash
# Host-side live trial of the slow loops (scout, tactical, strategy) in the running
# round, for about an hour: start / report / stop. The trial turns the manager's model
# gate on; stop turns it off again and restores the policy table and the picture to
# their pre-trial state, so no Round 2 tuning leaks into Round 3. Model-call rows and
# policy-change rows stay in the events ledger as evidence. Scheduled once via
# /etc/cron.d/ax-week08-r3-trial (2026-11-01 09:00-10:00 KST); stop removes that file.
set -euo pipefail
BASE=/docker/ax-week08-25620027
ENVFILE="$BASE/private/model-market.env"
DATA="$BASE/data/r3"
STATE="$BASE/trial-r3.state"
LOG="$BASE/trial-r3.log"
RELEASE="${2:-$(basename "$(readlink -f "$BASE/current")")}"
FILES="r3-policy.json r3-picture.json"
exec >>"$LOG" 2>&1
echo "== $(date -u +%FT%TZ) trial-r3 ${1:-} release=$RELEASE"
case "${1:-}" in
start)
  if [ -f "$STATE" ]; then echo "a trial is already running: $(cat "$STATE")"; exit 1; fi
  TS=$(date -u +%Y%m%dT%H%M%SZ); BK="$BASE/trial-$TS"; mkdir -p "$BK"
  for f in $FILES; do
    if [ -f "$DATA/$f" ]; then cp -p "$DATA/$f" "$BK/$f.before"; else : > "$BK/$f.absent"; fi
  done
  # make every cadence due on the first tick instead of waiting up to an hour
  python3 - "$DATA/r3-events.sqlite" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1], timeout=10) as db:
    db.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)")
    for key in ("scout_last_at", "tactical_last_at", "strategy_last_at"):
        db.execute("INSERT OR REPLACE INTO kv(key, value) VALUES (?, '0')", (key,))
PY
  sed -i 's/^AX_R3_MANAGER_MODEL=.*/AX_R3_MANAGER_MODEL=1/' "$ENVFILE"
  grep -q '^AX_R3_MANAGER_MODEL=1$' "$ENVFILE"
  "$BASE/launch-r3.sh" "$RELEASE"
  printf 'started_utc=%s\nstarted_epoch=%s\nbackup=%s\nrelease=%s\n' "$TS" "$(date -u +%s)" "$BK" "$RELEASE" > "$STATE"
  echo "trial started at $TS UTC; backup $BK"
  ;;
report)
  [ -f "$STATE" ] || { echo "no trial running"; exit 1; }
  started_epoch=$(sed -n 's/^started_epoch=//p' "$STATE")
  python3 "$BASE/trial_report.py" --data "$DATA" --since "$started_epoch" --kit-log "$DATA/logs/market-kit.log"
  ;;
stop)
  [ -f "$STATE" ] || { echo "no trial running"; exit 1; }
  started_epoch=$(sed -n 's/^started_epoch=//p' "$STATE")
  backup=$(sed -n 's/^backup=//p' "$STATE")
  release=$(sed -n 's/^release=//p' "$STATE")
  python3 "$BASE/trial_report.py" --data "$DATA" --since "$started_epoch" --kit-log "$DATA/logs/market-kit.log" | tee "$backup/report.json"
  sed -i 's/^AX_R3_MANAGER_MODEL=.*/AX_R3_MANAGER_MODEL=0/' "$ENVFILE"
  grep -q '^AX_R3_MANAGER_MODEL=0$' "$ENVFILE"
  for f in $FILES; do
    if [ -f "$backup/$f.before" ]; then
      if [ -f "$DATA/$f" ]; then cp -p "$DATA/$f" "$backup/$f.after"; fi
      cp -p "$backup/$f.before" "$DATA/$f"; chown 10001:10001 "$DATA/$f"; chmod 600 "$DATA/$f"
    elif [ -f "$DATA/$f" ]; then
      mv "$DATA/$f" "$backup/$f.after"   # absent before the trial: take it out again
    fi
  done
  "$BASE/launch-r3.sh" "$release"
  mv "$STATE" "$backup/trial-r3.state"
  rm -f /etc/cron.d/ax-week08-r3-trial
  echo "trial stopped; manager model off; policy and picture restored; results in $backup"
  ;;
*)
  echo "usage: $0 start|report|stop [release]"; exit 2
  ;;
esac
