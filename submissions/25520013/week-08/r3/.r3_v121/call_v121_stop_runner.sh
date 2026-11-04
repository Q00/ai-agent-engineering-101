# v121b: stop the labpc runner only (STOP file, then SIGTERM to the runner pid if needed). NOT RUN.
# Route: ssh labpc "wsl bash -l -s" < call_v121_stop_runner.sh   (also called by call_v121_rollback.sh)
# Touches only $W/.r3_runner_v121/. Kit, tmux, buyer, seller, autostart untouched. No SIGKILL (a hung runner -> human).
set -u
W=/home/sungsoo/ai-agent-engineering-101/submissions/25520013/week-09
D="$W/.r3_runner_v121"
echo "STOPRUNNER START $(date "+%F %T %Z")"
mkdir -p "$D" && : > "$D/STOP" && echo "ACT touched $D/STOP"
isrun(){ [ -n "$1" ] && tr "\0" " " < /proc/$1/cmdline 2>/dev/null | grep -q "tools/runner_v121.py"; }
RP=$(cat "$D/runner.pid" 2>/dev/null)
for _ in $(seq 1 30); do isrun "$RP" || break; sleep 2; done
if isrun "$RP"; then echo "ACT SIGTERM runner pid $RP (pid only)"; kill -TERM "$RP"; for _ in $(seq 1 15); do isrun "$RP" || break; sleep 2; done; fi
OTHER=$(pgrep -f "tools/runner_v121.py" | tr "\n" " ")
if isrun "$RP" || [ -n "$OTHER" ]; then echo "RESULT runner_still_running pid=$RP pgrep=[$OTHER] (no SIGKILL; human)"; exit 5; fi
echo "RESULT runner_stopped (STOP file stays; remove it to allow a new start)"; tail -n 2 "$D/runner.jsonl" 2>/dev/null | cut -c1-200
exit 0
