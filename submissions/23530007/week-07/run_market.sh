#!/usr/bin/env bash
# Keeps the market kit (shop + buyer as yms979) in the class market on this Ubuntu server.
#
#   run_market.sh start    start in a tmux session "market" and start again after a reboot (crontab @reboot)
#   run_market.sh stop     leave the market: stop the kit, its seller and tunnel, and remove the @reboot line
#   run_market.sh status   is it running, which shop URL, does the shop answer, last kit log lines
#   run_market.sh run      the supervisor loop itself, in the foreground (what start runs inside tmux)
#
# The loop
#   - starts market_kit.py join --tunnel and starts it again whenever it exits,
#   - waits 15 min when the quick tunnel is rate limited (429, or no trycloudflare URL), and after
#     3 short runs in a row, so restarts do not keep asking cloudflared for new tunnels,
#   - checks every 5 min that the shop answers at its tunnel URL, and restarts the kit after 3 misses
#     (the kit does not notice a dead seller or tunnel by itself).
#
# Claim code: MARKET_OWNER, or the file ~/.config/ai-agent-market/owner (outside git, chmod 600).
# Kit log: logs/market-kit.log (written by the kit; rotated chunks kept in logs/archive/). This loop: logs/market-supervisor.log.
set -u

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
NAME=yms979
MARKET=https://market.ourolabs.ai
SESSION=market
PY=$HERE/.venv/bin/python
OWNER_FILE=$HOME/.config/ai-agent-market/owner
STATE=$HERE/.market                        # gitignored
RUN_OUT=$STATE/kit-run.out                 # stdout/stderr of the current kit run
PIDFILE=$STATE/supervisor.pid
SUPLOG=$HERE/logs/market-supervisor.log

RATE_LIMIT_WAIT=900     # 15 min
RETRY_WAIT=30
SHORT_RUN=120           # a run shorter than this counts as short
CHECK_EVERY=300         # 5 min
MAX_MISSES=3

export PATH="$HERE/.venv/bin:$HOME/.local/bin:$PATH"   # agent.json runs plain "python"; claude and cloudflared are in ~/.local/bin
export PYTHONUNBUFFERED=1

say() { echo "$(date '+%F %T') [supervisor] $*" | tee -a "$SUPLOG"; }

# the first URL, or the one kit v3's watchdog registered after replacing a dead tunnel
shop_url() { grep -oE '(others reach it through the kit at|registered again as .+ at) https://[a-z0-9-]+\.trycloudflare\.com' "$RUN_OUT" 2>/dev/null | tail -1 | awk '{print $NF}'; }

# kit v3 moves market-kit.log to market-kit.log.1 at 20 MB and overwrites .log.1 at the next move,
# so keep each one: logs/archive/market-kit-<time>.log.gz (contents unchanged)
archive_rotated() {
    local f=$HERE/logs/market-kit.log.1
    [ -s "$f" ] || return 0
    mkdir -p "$HERE/logs/archive"
    local dst; dst=$HERE/logs/archive/market-kit-$(date '+%Y%m%d-%H%M%S').log
    mv "$f" "$dst" && gzip -9 "$dst" && say "archived a rotated kit log to logs/archive/$(basename "$dst").gz"
}

shop_answers() { curl -fsS -m 15 -o /dev/null "$1/.well-known/agent-card.json" 2>/dev/null; }

# only the end of the run, and only 429 as a status: trade lines carry numbers like "ledger #429"
rate_limited() { tail -n 30 "$RUN_OUT" | grep -qE 'Too Many Requests|did not print a trycloudflare URL|(status|code|HTTP)[^0-9]{0,3}429'; }

kit_pid=""
stop_kit() {
    [ -n "$kit_pid" ] && kill -0 "$kit_pid" 2>/dev/null || return 0
    kill -TERM "$kit_pid" 2>/dev/null       # the kit stops its seller and cloudflared on SIGTERM
    for _ in $(seq 30); do kill -0 "$kit_pid" 2>/dev/null || break; sleep 1; done
    kill -KILL "$kit_pid" 2>/dev/null
    wait "$kit_pid" 2>/dev/null
    kit_pid=""
}

run_loop() {
    mkdir -p "$STATE" "$HERE/logs"
    if [ -z "${MARKET_OWNER:-}" ] && [ -s "$OWNER_FILE" ]; then MARKET_OWNER=$(tr -d '[:space:]' < "$OWNER_FILE"); fi
    if [ -z "${MARKET_OWNER:-}" ]; then
        say "no claim code: set MARKET_OWNER or write it to $OWNER_FILE (from $MARKET/join)"; exit 1
    fi
    export MARKET_OWNER
    for bin in claude cloudflared; do command -v "$bin" >/dev/null || { say "$bin not on PATH"; exit 1; }; done
    [ -x "$PY" ] || { say "no venv at $HERE/.venv"; exit 1; }

    echo $$ > "$PIDFILE"
    trap 'say "stopping"; stop_kit; rm -f "$PIDFILE"; exit 0' INT TERM HUP
    local short=0
    while true; do
        : > "$RUN_OUT"
        say "starting the kit"
        local started=$SECONDS
        # own session: a tmux hangup or Ctrl-C reaches only this loop, which then stops the kit cleanly
        setsid "$PY" "$REPO/scripts/market_kit.py" join --dir "$HERE" --market "$MARKET" --name "$NAME" --tunnel \
            > "$RUN_OUT" 2>&1 &
        kit_pid=$!
        local misses=0 next_check=$((SECONDS + CHECK_EVERY))
        while kill -0 "$kit_pid" 2>/dev/null; do
            sleep 5
            archive_rotated
            [ $SECONDS -ge $next_check ] || continue
            next_check=$((SECONDS + CHECK_EVERY))
            local url; url=$(shop_url)
            [ -n "$url" ] || continue        # the tunnel is still coming up
            if shop_answers "$url"; then misses=0; else
                misses=$((misses + 1)); say "shop did not answer at $url ($misses/$MAX_MISSES)"
                if [ $misses -ge $MAX_MISSES ]; then say "restarting the kit"; stop_kit; fi
            fi
        done
        wait "$kit_pid" 2>/dev/null; local code=$?; kit_pid=""
        local ran=$((SECONDS - started))
        say "kit exited (code $code) after ${ran}s; last output: $(tail -3 "$RUN_OUT" | tr '\n' ' ')"
        if [ $ran -lt $SHORT_RUN ]; then short=$((short + 1)); else short=0; fi
        if rate_limited; then
            say "tunnel or market rate limit; trying again in 15 min"; sleep $RATE_LIMIT_WAIT
        elif [ $short -ge 3 ]; then
            say "$short short runs in a row; trying again in 15 min"; short=0; sleep $RATE_LIMIT_WAIT
        else
            sleep $RETRY_WAIT
        fi
    done
}

running() { [ -s "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

CRON_LINE="@reboot sleep 60 && /usr/bin/tmux new-session -d -s $SESSION '$HERE/run_market.sh run'"

case "${1:-}" in
    run) run_loop ;;
    start)
        if running; then echo "already running (pid $(cat "$PIDFILE"))"; exit 0; fi
        tmux new-session -d -s "$SESSION" "'$HERE/run_market.sh' run" || exit 1
        ( crontab -l 2>/dev/null | grep -vF "$HERE/run_market.sh"; echo "$CRON_LINE" ) | crontab -
        sleep 3; running && echo "started in tmux session '$SESSION' (tmux attach -t $SESSION); @reboot added" \
                         || { echo "did not start:"; tail -5 "$SUPLOG"; exit 1; } ;;
    stop)
        ( crontab -l 2>/dev/null | grep -vF "$HERE/run_market.sh" ) | crontab -
        if running; then
            pid=$(cat "$PIDFILE"); kill -TERM "$pid"
            for _ in $(seq 40); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
            echo "stopped"
        else echo "not running"; fi
        tmux kill-session -t "$SESSION" 2>/dev/null; true ;;
    status)
        if running; then echo "running (pid $(cat "$PIDFILE"))"; else echo "not running"; fi
        url=$(shop_url)
        if [ -n "$url" ]; then
            if shop_answers "$url"; then echo "shop: $url answers"; else echo "shop: $url does NOT answer"; fi
        fi
        crontab -l 2>/dev/null | grep -qF "$HERE/run_market.sh" && echo "@reboot: on" || echo "@reboot: off"
        echo "--- logs/market-kit.log"; tail -5 "$HERE/logs/market-kit.log" 2>/dev/null ;;
    *) sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
