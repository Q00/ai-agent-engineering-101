#!/usr/bin/env bash
# Watches for what Round 3 depends on and writes it to logs/round-watch.log (cron, every 15 min).
#
#   watch_round.sh          one check (what cron runs)
#   watch_round.sh install  add the cron line        watch_round.sh remove   remove it
#
# Each check
#   - upstream main (the course repo): new commits are fetched, listed and merged into the current branch
#     (merge, never rebase; aborted on a conflict); lines marked !! when they touch weeks/week-08/,
#     scripts/check_week08.py or the kit, and a kit change stops the kit so run_market.sh restarts it on the new one;
#     with our own market_kit.py the change is ported by re-applying kit.patch (our 3 changes), and if that patch
#     does not apply the copy is set aside so the course kit runs (tool payment off, the kit pays),
#   - the market round (/api/board "current"): on a change, saves the board of the round that just ended
#     to logs/board-round<N>-final-<time>.json, the "before" side of the price analysis,
#   - health: run_market.sh not running, or no market result line in the kit log for 30 min; and recovery.
# Every !! line is also sent to the user once, through notify.py (mail and/or ntfy push).
set -u

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# cron starts with a bare PATH: the strategist calls claude (~/.local/bin), the same as run_market.sh sets
export PATH="$HERE/.venv/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
REPO=$(cd "$HERE/../../.." && pwd)
MARKET=https://market.ourolabs.ai
STATE=$HERE/.market/watch.state               # gitignored: "<upstream hash> <round> <health>"
LOG=$HERE/logs/round-watch.log
CRON_LINE="*/15 * * * * $HERE/watch_round.sh >/dev/null 2>&1"

ALERTS=()
say() { echo "$(date '+%F %T') $*" >> "$LOG"; case "$*" in '!!'*) ALERTS+=("${*#!! }") ;; esac; }

case "${1:-}" in
    install) ( crontab -l 2>/dev/null | grep -vF "$HERE/watch_round.sh"; echo "$CRON_LINE" ) | crontab -
             echo "cron: every 15 min"; exit 0 ;;
    remove)  ( crontab -l 2>/dev/null | grep -vF "$HERE/watch_round.sh" ) | crontab -; echo "removed"; exit 0 ;;
esac

mkdir -p "$HERE/.market" "$HERE/logs"
read -r old_hash old_round old_health 2>/dev/null < "$STATE" || { old_hash=""; old_round=""; old_health=""; }

hash=$(git -C "$REPO" ls-remote upstream refs/heads/main 2>/dev/null | cut -f1)
if [ -n "$hash" ] && [ "$hash" != "$old_hash" ]; then
    git -C "$REPO" fetch -q upstream main 2>/dev/null
    if [ -n "$old_hash" ]; then
        say "upstream main moved: $(git -C "$REPO" log --oneline "$old_hash..$hash" | tr '\n' ';')"
        files=$(git -C "$REPO" diff --name-only "$old_hash" "$hash")
        echo "$files" | grep -qE '^weeks/week-08/' && say "!! weeks/week-08/ changed (assignment spec): $(echo "$files" | grep '^weeks/week-08/' | tr '\n' ' ')"
        echo "$files" | grep -qE '^scripts/check_week08\.py$' && say "!! scripts/check_week08.py changed (CI check)"
        # apply it: merge (never rebase; history is grading evidence), abort and leave things as they were on a conflict
        if git -C "$REPO" merge -q --no-edit upstream/main >/dev/null 2>&1; then
            say "merged upstream/main into $(git -C "$REPO" branch --show-current)"
            if echo "$files" | grep -qE '^scripts/market_kit\.py$'; then
                ver=$(grep -m1 '^KIT_VERSION' "$REPO/scripts/market_kit.py")
                if [ -f "$HERE/market_kit.py" ]; then        # our copy: re-apply kit.patch to the new course kit
                    new=$HERE/.market/market_kit.new.py
                    cp "$REPO/scripts/market_kit.py" "$new"
                    if [ -s "$HERE/kit.patch" ] && patch -s -F0 --no-backup-if-mismatch -r - "$new" < "$HERE/kit.patch" >/dev/null 2>&1 \
                            && "$HERE/.venv/bin/python" -m py_compile "$new" 2>/dev/null; then
                        cp "$HERE/market_kit.py" "$HERE/.market/market_kit.prev.py"
                        mv "$new" "$HERE/market_kit.py"
                        diff -u "$REPO/scripts/market_kit.py" "$HERE/market_kit.py" > "$HERE/kit.patch"
                        say "!! the course kit changed ($ver); our changes (kit.patch) re-applied to it, restarting the kit"
                    else
                        rm -f "$new"
                        mv "$HERE/market_kit.py" "$HERE/market_kit.py.disabled"
                        say "!! the course kit changed ($ver); kit.patch does not apply, so the course kit runs now (tool payment off, the kit pays); port market_kit.py.disabled by hand"
                    fi
                fi
                # the kit process only: a pattern match on the command line would also hit this shell
                kit=$(ps -eo pid,args | awk -v d="$HERE" '$2 ~ /\/python$/ && index($0, "market_kit.py join --dir " d) {print $1}' | head -1)
                [ -n "$kit" ] && kill -TERM "$kit"
                say "!! stopped kit pid ${kit:-none}; run_market.sh starts it again on the new kit"
            fi
        else
            git -C "$REPO" merge --abort >/dev/null 2>&1
            say "!! merging upstream/main failed and was aborted; nothing changed, merge by hand"
        fi
    else
        say "watching upstream main from $hash"
    fi
fi

board=$(curl -fsS -m 20 "$MARKET/api/board" 2>/dev/null)
round=$(echo "$board" | python3 -c 'import json,sys; print(json.load(sys.stdin)["current"])' 2>/dev/null)
if [ -n "$round" ] && [ "$round" != "$old_round" ]; then
    if [ -n "$old_round" ]; then
        snap=$HERE/logs/board-round$old_round-final-$(date '+%Y%m%d-%H%M').json
        curl -fsS -m 30 "$MARKET/api/board?round=$old_round" -o "$snap" 2>/dev/null
        say "!! market round $old_round -> $round; board of round $old_round saved to logs/$(basename "$snap")"
    else
        say "watching the market from round $round"
    fi
fi

health=ok; why=""
pid=$(cat "$HERE/.market/supervisor.pid" 2>/dev/null)
if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
    health=down; why="run_market.sh is not running; start it with run_market.sh start"
else
    last=$(grep -h '\[market\] ledger #' "$HERE/logs/market-kit.log" 2>/dev/null | tail -1 | cut -c1-8)
    if [ -n "$last" ]; then                       # none yet right after a log rotation: skip
        age=$(( ( $(date +%s) - $(date -d "$last" +%s) + 86400 ) % 86400 ))
        [ "$age" -gt 1800 ] && { health=stalled; why="no market result in the kit log for $((age / 60)) min; see run_market.sh status"; }
    fi
fi
if [ "$health" != "${old_health:-ok}" ]; then
    if [ "$health" = ok ]; then say "!! recovered: the kit reports market results again"; else say "!! $health: $why"; fi
fi

if [ ${#ALERTS[@]} -gt 0 ]; then
    status=$(echo "$board" | python3 -c '
import json, sys
b = json.load(sys.stdin)
def row(L):
    return next((x for x in L if x["name"] == "yms979"), None)
s, u = row(b["shops"]), row(b["buyers"])
shop = "%s deals, profit %s, stars %s, alive %s" % (s["deals"], s["profit"], s["stars"], s["alive"]) if s else "not on the board"
buyer = "%s deals, alive %s" % (u["deals"], u["alive"]) if u else "not on the board"
print("round %s; shop yms979: %s; buyer yms979: %s" % (b["current"], shop, buyer))' 2>/dev/null)
    body=$(printf '%s\n' "${ALERTS[@]}"; echo; echo "${status:-market board not reachable}"; echo "log: $LOG")
    sent=$("$HERE/.venv/bin/python" "$HERE/notify.py" "[yms979] ${ALERTS[0]:0:80}" "$body" 2>/dev/null)
    say "sent ${#ALERTS[@]} alert(s) via: ${sent:-none}"
fi

echo "${hash:-$old_hash} ${round:-$old_round} $health" > "$STATE"

# strategist: market analysis and parameter moves, once an hour (strategist.py keeps its own clock); in the
# background so a slow model call never holds this watcher up
"$HERE/.venv/bin/python" "$HERE/buyer_score.py" >/dev/null 2>&1 || true          # 11-11: scorekeeper (m*, calibration, reconcile)
"$HERE/.venv/bin/python" "$HERE/market_probe.py" --check >/dev/null 2>&1 || true   # its watch list may wake it early
nohup "$HERE/.venv/bin/python" "$HERE/strategist.py" >/dev/null 2>&1 &
nohup "$HERE/.venv/bin/python" "$HERE/buyer_strategist.py" >/dev/null 2>&1 &   # the buyer's own analyst
# the curator (memory + market intelligence) also runs on every tick: refreshes its market picture and briefing
# when the digest is an hour old even if no strategist posted, and sees rule patches within 15 min
nohup "$HERE/.venv/bin/python" "$HERE/memory_curator.py" >/dev/null 2>&1 &

# 11-08: the user's phone messages (private ntfy topic) read every 30 s and passed to the agents
nohup "$HERE/.venv/bin/python" "$HERE/inbox.py" --loop 14 30 >/dev/null 2>&1 &

# 11-08: the market's rules read every ~90 s until the next tick (user: answer rule updates fast); one loop at a time
nohup "$HERE/.venv/bin/python" "$HERE/rules_fast.py" --loop 14 90 >/dev/null 2>&1 &

# 11-07: book-lead guard for the shop warranty (cuts B, then A, when the lead over the next shop's book shrinks)
"$HERE/.venv/bin/python" "$HERE/shop_guard.py" >/dev/null 2>&1 || true

# hourly status push for the user (summary.py sends at most once an hour)
"$HERE/.venv/bin/python" "$HERE/summary.py" >/dev/null 2>&1 || true
