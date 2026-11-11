#!/bin/bash
# Attempt-2 background processes on labpc: memory daemon, memory feeder, operator (single, or the two role operators
# operator-shop and operator-buyer; never both kinds at once), and the investigator analyst (kernel.analyst, started by
# name only: a2ctl.sh start analyst).
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
  start)  # a2ctl.sh start [memd] [memfeed] [operator] [operator-shop] [operator-buyer] [analyst]; no names = memd memfeed operator-shop operator-buyer
    names="${*:2}"; names="${names:-memd memfeed operator-shop operator-buyer}"
    for n in $names; do
      case "$n" in
        memd) launch memd "$CODE/.venv/bin/python" -m memd.server --root "$W" ;;
        memfeed) launch memfeed python3 -m kernel.memfeed --root "$W" --interval-s 300 ;;
        operator-shop|operator-buyer)  # role operators: the single operator's environment plus the role
          if alive operator; then echo "$n refused: the single operator is running (stop operator first)"; continue; fi
          r=${n#operator-}
          steps=60  # 11-11: a silent runaway backstop, not shown to the model; the operator ends its wake itself
          launch "$n" env A2_COMPACT_STEPS="${A2_COMPACT_STEPS:-$steps}" A2_OPERATOR_EFFORT="${A2_OPERATOR_EFFORT:-medium}" A2_AUDITOR="${A2_AUDITOR:-1}" A2_OPERATOR_INTERVAL_S="${A2_OPERATOR_INTERVAL_S:-1200}" A2_OPERATOR_COMPACT="${A2_OPERATOR_COMPACT:-1}" A2_SPLIT="${A2_SPLIT:-1}" A2_SELLER_RESTART_CMD="${A2_SELLER_RESTART_CMD:-bash $CODE/live/seller_restart.sh}" A2_CODE_TESTS_DIR="${A2_CODE_TESTS_DIR:-$HOME/r3-rule-tests}" A2_OPERATOR_ROLE="$r" python3 -m kernel.operator --root "$W" --role "$r" ;;
        operator)  # the single operator; never next to the role operators
          if alive operator-shop || alive operator-buyer; then echo "operator refused: a role operator is running (stop operator-shop operator-buyer first)"; continue; fi
          launch operator env A2_OPERATOR_EFFORT="${A2_OPERATOR_EFFORT:-medium}" A2_AUDITOR="${A2_AUDITOR:-1}" A2_OPERATOR_INTERVAL_S="${A2_OPERATOR_INTERVAL_S:-1200}" A2_OPERATOR_COMPACT="${A2_OPERATOR_COMPACT:-1}" A2_SPLIT="${A2_SPLIT:-1}" A2_SELLER_RESTART_CMD="${A2_SELLER_RESTART_CMD:-bash $CODE/live/seller_restart.sh}" A2_CODE_TESTS_DIR="${A2_CODE_TESTS_DIR:-$HOME/r3-rule-tests}" python3 -m kernel.operator --root "$W" ;;
        analyst)  # the investigator: read-only, never takes the operators' busy lock
          launch analyst env A2_ANALYST_STEPS="${A2_ANALYST_STEPS:-60}" A2_ANALYST_EFFORT="${A2_ANALYST_EFFORT:-medium}" A2_ANALYST_MIN_GAP_S="${A2_ANALYST_MIN_GAP_S:-900}" python3 -m kernel.analyst --root "$W" ;;
        *) echo "unknown process $n" ;;
      esac
    done
    ;;
  stop)  # a2ctl.sh stop [names]; no names = every operator, the analyst, memfeed, memd
    names="${*:2}"; names="${names:-operator operator-shop operator-buyer analyst memfeed memd}"
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
    for n in memd memfeed operator operator-shop operator-buyer analyst; do
      if alive "$n"; then echo "$n running pid $(cat "$P/$n.pid")"; else echo "$n not running"; fi
    done
    [ -e "$W/a2/LIVE" ] && echo "switch: LIVE (attempt 2 decides)" || echo "switch: off (attempt 1 decides)"
    ;;
  *) echo "usage: $0 start|stop|status"; exit 2 ;;
esac
