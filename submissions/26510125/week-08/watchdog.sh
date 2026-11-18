#!/usr/bin/env bash
# Keep the kit alive for the whole of Round 3: every 5 min, restart it if the process is gone or the kit log
# has been silent for 20 min (dead tunnel the kit could not replace, hung process, reboot).
#   setsid nohup ./watchdog.sh > logs/watchdog.log 2>&1 &
HERE="$(cd "$(dirname "$0")" && pwd)"; LOG="$HERE/logs/market-kit.log"
while true; do
  pid=$(cat "$HERE/kit.pid" 2>/dev/null)
  alive=0; [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && alive=1
  age=$(( $(date +%s) - $(stat -c %Y "$LOG" 2>/dev/null || echo 0) ))
  if [ "$alive" = 0 ] || [ "$age" -gt 1200 ]; then
    echo "$(date '+%F %T') restart: alive=$alive log_age=${age}s" 
    [ -n "$pid" ] && kill "$pid" 2>/dev/null; sleep 5
    "$HERE/run_kit.sh" --background
    sleep 180            # tunnel DNS takes 1-2 min; do not judge the new kit before that
  fi
  sleep 300
done
