#!/usr/bin/env bash
# Run by kiro-wait-r3.service (root) on the Azure VM: waits for Round 3. Once an hour, at minute 05
# (10:05, 11:05, ... KST), it asks the market's public board (GET /api/board, "current") which round is
# on; at 3 it enables and starts the kit and the analysis runner (so a reboot keeps them running) and
# disables itself. Until then nothing of ours joins the market, so Round 2 orders and records stay out of
# the system's memory.
set -u
board() {
  curl -s -m 20 https://market.ourolabs.ai/api/board | python3 -c "import json, sys; print(json.load(sys.stdin).get('current'))" 2>/dev/null
}
next_slot() {  # the next hh:05 strictly after now (KST is a whole number of hours off UTC)
  local now=${1:-$(date +%s)}
  echo $(( now + (299 - now % 3600 + 3600) % 3600 + 1 ))
}
at() { TZ=Asia/Seoul date -d @"$1" '+%H:%M %Z'; }
[ "${1:-}" = "--slot" ] && { next_slot "$2"; exit 0; }  # for testing the schedule
next=$(next_slot)
echo "$(date -u +%FT%TZ) waiting for Round 3; first check at $(at "$next"), then every hour"
while true; do
  sleep $(( next - $(date +%s) ))
  for try in 1 2 3; do  # a board that does not answer gets two more tries, a minute apart
    r=$(board)
    case "$r" in ""|None) sleep 60 ;; *) break ;; esac
  done
  if [ "$r" = "3" ]; then
    echo "$(date -u +%FT%TZ) round 3 is on: starting kiro-kit and kiro-analysis"
    systemctl enable --now kiro-kit.service kiro-analysis.service
    systemctl disable kiro-wait-r3.service
    exit 0
  fi
  next=$(next_slot)
  echo "$(date -u +%FT%TZ) round ${r:-unknown}; next check at $(at "$next")"
done
