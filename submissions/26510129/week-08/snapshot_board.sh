#!/usr/bin/env bash
# Save the public board around the close of Round 3 (2026-11-17 23:59 KST): the ranking part of the midterm is read
# from the board at that moment (weeks/week-11/README.md). Run detached before the close:
#   setsid nohup ./snapshot_board.sh > logs/board-snapshot.log 2>&1 < /dev/null &
# Writes logs/board-<mmdd-HHMMSS>.json (the raw /api/board) and one summary line per snapshot to stdout.
DIR=$(cd "$(dirname "$0")" && pwd)
NAME=${MARKET_NICK:-jiseokkk}
for at in "2026-11-17 23:50:00" "2026-11-17 23:58:30" "2026-11-17 23:59:40" "2026-11-18 00:01:00" "2026-11-18 00:10:00"; do
  wait_s=$(( $(date -d "$at" +%s) - $(date +%s) ))
  [ "$wait_s" -gt 0 ] && sleep "$wait_s"
  out="$DIR/logs/board-$(date +%m%d-%H%M%S).json"
  curl -s -m 40 https://market.ourolabs.ai/api/board > "$out" || { echo "$(date '+%F %T') fetch failed"; continue; }
  python3 - "$out" "$NAME" <<'PY'
import json, sys, time
try:
    b = json.load(open(sys.argv[1]))
except Exception as e:
    print(time.strftime("%F %T"), "unreadable board:", e); sys.exit(0)
name = sys.argv[2]
line = [time.strftime("%F %T")]
for side, keys in (("shops", ("all", "book", "revenue", "stars", "deals")), ("buyers", ("all", "saved", "deals", "posts", "forum"))):
    rows = sorted((r for r in b.get(side, []) if not r.get("house")), key=lambda r: -(r.get("all") or 0))
    for i, r in enumerate(rows):
        if r.get("name") == name:
            line.append(f"{side} rank {i + 1}/{len(rows)} " + " ".join(f"{k}={r.get(k)}" for k in keys))
for p in b.get("players", []):
    if p.get("player") == name:
        line.append(f"players total={p.get('total')} student_total={p.get('student_total')} student_pct={p.get('student_pct')}")
print(" | ".join(line), flush=True)
PY
done
