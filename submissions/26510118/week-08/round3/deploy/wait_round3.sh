#!/usr/bin/env bash
# Run by kiro-wait-r3.service (root) on the Azure VM: waits for Round 3. Every minute (EVERY seconds) it
# asks the market's public board (GET /api/board, "current") which round is on; at 3 it enables and
# starts the kit and the analysis runner (so a reboot keeps them running) and disables itself. Until then
# nothing of ours joins the market, so Round 2 orders and records stay out of the system's memory.
# (11/3: once an hour from 10:05, then back to every minute on the evening Round 3 was expected.)
# The log gets a line when the answer changes, and one an hour while it does not.
set -u
EVERY=${EVERY:-60}
board() {
  curl -s -m 20 https://market.ourolabs.ai/api/board | python3 -c "import json, sys; print(json.load(sys.stdin).get('current'))" 2>/dev/null
}
last="" said=0
echo "$(date -u +%FT%TZ) waiting for Round 3; checking every ${EVERY} s"
while true; do
  r=$(board)
  if [ "$r" = "3" ]; then
    echo "$(date -u +%FT%TZ) round 3 is on: starting kiro-kit and kiro-analysis"
    systemctl enable --now kiro-kit.service kiro-analysis.service
    systemctl disable kiro-wait-r3.service
    exit 0
  fi
  if [ "$r" != "$last" ] || [ $(( $(date +%s) - said )) -ge 3600 ]; then
    echo "$(date -u +%FT%TZ) round ${r:-unknown}"
    last=$r said=$(date +%s)
  fi
  sleep "$EVERY"
done
