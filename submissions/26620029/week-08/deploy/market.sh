#!/bin/bash
# Operate the kit on the server: market.sh start | stop | restart | status | logs | watchdog | deadline-stop
set -euo pipefail
ROOT=/srv/market
DIR="$ROOT/repo/submissions/26620029/week-08/deploy"
FLAG="$ROOT/control/STOPPED"
AGENT="$ROOT/repo/submissions/26620029/week-08"
DEADLINE=$(date -d '2026-11-18 00:05 KST' +%s)
dc() { docker compose --env-file "$ROOT/control/compose.env" -f "$DIR/compose.yaml" "$@"; }

case "${1:-status}" in
  start)
    [ "$(date +%s)" -ge "$DEADLINE" ] && [ "${2:-}" != "--after-deadline" ] && { echo "Round 3 is over; add --after-deadline to start anyway"; exit 1; }
    rm -f "$FLAG"; dc up -d --build ;;
  stop)     touch "$FLAG"; dc stop ;;                # the flag keeps the watchdog and a reboot from starting it again
  restart)  [ -e "$FLAG" ] && { echo "STOPPED flag set; use start"; exit 1; }; dc restart kit ;;
  logs)     tail -n "${2:-50}" "$AGENT/logs/market-kit.log" ;;
  status)
    echo "flag: $([ -e "$FLAG" ] && echo STOPPED || echo running-allowed)"
    docker ps -a --filter name=market-kit --format '{{.Names}} {{.Status}}'
    docker stats --no-stream --format 'mem {{.MemUsage}} cpu {{.CPUPerc}}' market-kit 2>/dev/null || true
    free -m | sed -n 1,3p
    df -h "$ROOT" | tail -1
    echo "log age: $(( $(date +%s) - $(stat -c %Y "$AGENT/logs/market-kit.log") )) s"
    grep -a -E '\[tunnel\]|shop registered|buyer registered' "$AGENT/logs/market-kit.log" | tail -3 ;;
  watchdog)                                          # systemd timer, every 5 minutes
    [ -e "$FLAG" ] && exit 0
    state=$(docker inspect -f '{{.State.Status}} {{.State.Health.Status}}' market-kit 2>/dev/null || echo missing)
    case "$state" in
      "running healthy"|"running starting"|"restarting "*) ;;
      *) echo "$(date '+%m-%d %H:%M:%S') watchdog: '$state', restarting" >> "$ROOT/control/watchdog.log"; dc up -d kit ;;
    esac ;;
  deadline-stop)                                     # systemd timer, once at the end of Round 3
    touch "$FLAG"; dc stop
    echo "$(date '+%m-%d %H:%M:%S') stopped at the deadline" >> "$ROOT/control/watchdog.log" ;;
  *) echo "usage: market.sh start|stop|restart|status|logs [n]"; exit 2 ;;
esac
