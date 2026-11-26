#!/usr/bin/env bash
# Cron, every 2 minutes: switch to the Round 3 settings when the market itself says
# round 3, not at a fixed clock time (the course gives the date, not the hour; a
# clock switch would have let the policy loops tune on Round 2 numbers). Safety net:
# if Round 3 has not opened by 2026-11-03 22:00 KST, extend only the stop time so the
# run does not end at 23:59 that night; the policy loops stay off until round 3.
set -uo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
BASE=/docker/ax-week08-25620027
LOG="$BASE/arm-r3.log"
DONE="$BASE/r3-switched"
[ -f "$DONE" ] && exit 0
ROUND=$(curl -sS -m 15 https://market.ourolabs.ai/api/board 2>/dev/null \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("round") or 0)' 2>/dev/null || echo 0)
# The board timed out for minutes on 2026-11-03 evening while the market was
# reworked; the kit's own order lines carry the round too ("[order o-..] chair,
# budget 225, round 3"), so read the latest one when the board gives nothing.
if [ "$ROUND" = "0" ]; then
  ROUND=$(tail -n 400 "$BASE/data/r3/logs/market-kit.log" 2>/dev/null \
    | grep -a '^[0-9:]* \[order ' | tail -n 1 | sed -nE 's/.*, round ([0-9]+)(, .*)?$/\1/p')
  ROUND=${ROUND:-0}
fi
if [ "$ROUND" = "3" ]; then
  echo "== $(date -u +%FT%TZ) market round 3 detected; switching" >>"$LOG"
  if "$BASE/switch-r3.sh"; then
    touch "$DONE"
    echo "== $(date -u +%FT%TZ) switched" >>"$LOG"
  else
    echo "== $(date -u +%FT%TZ) switch failed; will retry" >>"$LOG"
  fi
  exit 0
fi
NOW=$(date -u +%FT%TZ)
if [ "$NOW" \> "2026-11-03T13:00:00Z" ] && grep -q '"stop_at": "2026-11-03' "$BASE/private/runtime.json"; then
  echo "== $NOW round $ROUND at 22:00 KST; extending stop_at only" >>"$LOG"
  python3 - "$BASE/private/runtime.json" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    settings = json.load(f)
settings["stop_at"] = "2026-11-17T23:59:59+09:00"
with open(path, "w", encoding="utf-8") as f:
    json.dump(settings, f, indent=1)
    f.write("\n")
PY
  chown 10001:10001 "$BASE/private/runtime.json"; chmod 600 "$BASE/private/runtime.json"
  "$BASE/launch-r3.sh" "$(basename "$(readlink -f "$BASE/current")")" >>"$LOG" 2>&1
fi
