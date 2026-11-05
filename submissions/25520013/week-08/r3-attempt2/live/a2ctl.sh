#!/bin/bash
# Attempt-2 background processes on labpc: memory daemon, memory feeder, operator.
#   a2ctl.sh start|stop|status   (CODE=~/r3a2-code, W=the live dir)
# Each process runs in its own session (setsid) and its pid is kept in W/a2/pids/<name>.pid; stop sends SIGTERM to
# that process group only. The switch for the hooks is the flag file W/a2/LIVE, not this script.
set -u
CODE="${CODE:-$HOME/r3a2-code}"
W="${W:-$HOME/ai-agent-engineering-101/submissions/25520013/week-09}"
P="$W/a2/pids"
mkdir -p "$P" "$W/a2/logs"

alive() { [ -f "$P/$1.pid" ] && kill -0 "$(cat "$P/$1.pid")" 2>/dev/null; }

launch() { # name, command...
  local n=$1; shift
  if alive "$n"; then echo "$n already running pid $(cat "$P/$n.pid")"; return; fi
  # the child writes its own pid: setsid forks when its caller leads a process group, so $! is not the process
  (cd "$CODE" && setsid bash -c 'echo $$ > "$0"; exec "$@"' "$P/$n.pid" "$@" >> "$W/a2/logs/$n.log" 2>&1 < /dev/null &)
  sleep 1
  alive "$n" && echo "$n started pid $(cat "$P/$n.pid")" || echo "$n FAILED to start (see a2/logs/$n.log)"
}

case "${1:-status}" in
  start)  # a2ctl.sh start [memd] [memfeed] [operator]; no names = all three
    names="${*:2}"; names="${names:-memd memfeed operator}"
    for n in $names; do
      case "$n" in
        memd) launch memd "$CODE/.venv/bin/python" -m memd.server --root "$W" ;;
        memfeed) launch memfeed python3 -m kernel.memfeed --root "$W" --interval-s 300 ;;
        operator) launch operator python3 -m kernel.operator --root "$W" ;;
        *) echo "unknown process $n" ;;
      esac
    done
    ;;
  stop)  # a2ctl.sh stop [names]; no names = all three
    names="${*:2}"; names="${names:-operator memfeed memd}"
    for n in $names; do
      if alive "$n"; then
        pg=$(ps -o pgid= -p "$(cat "$P/$n.pid")" | tr -d ' ')
        [ -n "$pg" ] && kill -TERM -- "-$pg" && echo "$n stopped (pgid $pg)"
      else
        echo "$n not running"
      fi
      rm -f "$P/$n.pid"
    done
    ;;
  status)
    for n in memd memfeed operator; do
      if alive "$n"; then echo "$n running pid $(cat "$P/$n.pid")"; else echo "$n not running"; fi
    done
    [ -e "$W/a2/LIVE" ] && echo "switch: LIVE (attempt 2 decides)" || echo "switch: off (attempt 1 decides)"
    ;;
  *) echo "usage: $0 start|stop|status"; exit 2 ;;
esac
