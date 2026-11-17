#!/bin/bash
# Loop watchdog for attempt 2: every SLEEP_S seconds, starts the a2 processes that are not running (via a2ctl.sh)
# and recreates the boardsnap window in tmux session "market". It never kills anything and never touches the
# kit/forum/runner windows. Run it in its own tmux window:  bash live/watchdog.sh
#   Stop switches: W/a2/WATCHDOG_OFF (everything), W/a2/stop/<name> (one name).
#   Cap: at most MAX_RESTARTS starts of one name per hour (W/a2/watchdog_state), then "giving up" for the hour.
#   One line per action in W/a2/logs/watchdog.log; when all is well, only one heartbeat line per hour.
#   Env overrides (for tests): W, CODE, A2CTL, TMUX_BIN, SLEEP_S, PROC, ONCE=1 (one pass, then exit).
set -u
CODE="${CODE:-$HOME/r3a2-code}"
W="${W:-$HOME/ai-agent-engineering-101/submissions/25520013/week-09}"
A2CTL="${A2CTL:-$CODE/live/a2ctl.sh}"
TMUX_BIN="${TMUX_BIN:-tmux}"
SLEEP_S="${SLEEP_S:-120}"
PROC="${PROC:-/proc}"
MAX_RESTARTS=3
NAMES="memd memfeed operator-shop operator-buyer analyst agents"
P="$W/a2/pids"
STATE="$W/a2/watchdog_state"  # lines: <epoch> <name> start|giveup, and <epoch> heartbeat
LOG="$W/a2/logs/watchdog.log"
mkdir -p "$W/a2/logs" "$P"
touch "$STATE"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

count() { # name, kind -> number of state lines of that kind for the name in the last hour
  awk -v n="$1" -v k="$2" '$2 == n && $3 == k' "$STATE" | wc -l | tr -d ' '
}

prune() { # drop state lines older than an hour
  awk -v c="$(( $(date +%s) - 3600 ))" '$1 >= c' "$STATE" > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
}

allowed() { # name -> 0 if another restart is allowed this hour; logs "giving up" once per hour otherwise
  if [ "$(count "$1" start)" -lt "$MAX_RESTARTS" ]; then return 0; fi
  if [ "$(count "$1" giveup)" -eq 0 ]; then
    log "$1 restarted $MAX_RESTARTS times in the last hour, giving up for the hour"
    echo "$(date +%s) $1 giveup" >> "$STATE"
  fi
  return 1
}

drop_stale_pid() { # name: a pid file whose process is not one of ours (pid reuse after reboot) is removed
  local f="$P/$1.pid" pid cmd
  [ -f "$f" ] || return 0
  pid=$(cat "$f")
  cmd=$(tr '\0' ' ' < "$PROC/$pid/cmdline" 2>/dev/null)
  case "$cmd" in
    *kernel.*|*memd*) ;;
    *) rm -f "$f"; log "$1 removed stale pid file (pid $pid: '${cmd}')" ;;
  esac
}

check_procs() {
  local st n out
  # stale pid files first: a reused pid passes a2ctl's kill -0 test, so status would call it running
  for n in $NAMES; do [ -e "$W/a2/stop/$n" ] || drop_stale_pid "$n"; done
  st=$(W="$W" CODE="$CODE" bash "$A2CTL" status 2>&1)
  for n in $NAMES; do
    echo "$st" | grep -q "^$n running" && continue
    [ -e "$W/a2/stop/$n" ] && continue
    allowed "$n" || continue
    out=$(W="$W" CODE="$CODE" A2_ANALYST_MIN_GAP_S="${A2_ANALYST_MIN_GAP_S:-600}" bash "$A2CTL" start "$n" 2>&1 | tr '\n' ' ')
    echo "$(date +%s) $n start" >> "$STATE"
    log "$n not running, started: $out"
  done
}

check_boardsnap() {
  "$TMUX_BIN" list-windows -t market -F '#{window_name}' 2>/dev/null | grep -qx boardsnap && return
  [ -e "$W/a2/stop/boardsnap" ] && return
  allowed boardsnap || return
  local out rc
  out=$("$TMUX_BIN" new-window -d -t market -n boardsnap "bash $W/.market/boardsnap-loop.sh" 2>&1); rc=$?
  echo "$(date +%s) boardsnap start" >> "$STATE"
  log "boardsnap window missing, recreated (exit $rc) $(echo "$out" | tr '\n' ' ')"
}

while true; do
  if [ ! -e "$W/a2/WATCHDOG_OFF" ]; then
    prune
    check_procs
    check_boardsnap
    if ! awk '$2 == "heartbeat"' "$STATE" | grep -q .; then
      log "heartbeat"
      echo "$(date +%s) heartbeat" >> "$STATE"
    fi
  fi
  [ "${ONCE:-0}" = 1 ] && break
  sleep "$SLEEP_S"
done
