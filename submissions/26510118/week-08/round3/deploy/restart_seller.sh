#!/bin/bash
# Restart the shop's seller to give back the memory it leaks (the user's call, 11/12).
#
# The seller grows about 6.7 MB an hour and never shrinks: RSS + swap was 104 MB 3.3 h after a restart and
# 389 MB after 46 h (11/10-11/12), on a server with 837 MB of RAM. The seller runs as the kit's grandchild
# (kit -> sh -> python seller.py); killing that python is enough, the kit's watchdog starts a new one on the
# same port and tunnel within about a minute. Talks open at that moment are lost, so it runs once a day.
#
#   cron (server time is UTC): 30 19 * * *  = 04:30 KST
#   30 19 * * * /home/azureuser/market/round3/deploy/restart_seller.sh >> /home/azureuser/market/round3/logs/seller_restarts.log 2>&1
#
# Prints one line: when, the old seller's pid, uptime and memory, and the new seller's pid (or that none came back).
set -u
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
seller_pid() {
    local kit sh
    kit=$(pgrep -f "market_kit.py joi[n]" | head -1)
    [ -z "$kit" ] && return
    sh=$(ps -o pid= -o cmd= --ppid "$kit" | awk '/seller\.py/ {print $1}' | head -1)
    [ -z "$sh" ] && return
    ps -o pid= --ppid "$sh" | tr -d ' ' | head -1
}

OLD=$(seller_pid)
if [ -z "$OLD" ]; then
    echo "$(now) no seller found under the kit; nothing done"
    exit 0
fi
MEM=$(awk '/^VmRSS|^VmSwap/ {s += $2} END {print int(s / 1024)}' "/proc/$OLD/status")
UP=$(ps -o etime= -p "$OLD" | tr -d ' ')
kill "$OLD"
NEW=""
for _ in $(seq 1 18); do                   # up to 3 minutes for the kit to start a new one
    sleep 10
    NEW=$(seller_pid)
    if [ -n "$NEW" ] && [ "$NEW" != "$OLD" ]; then
        break
    fi
    NEW=""
done
echo "$(now) seller $OLD (up $UP, RSS+swap ${MEM} MB) stopped; new seller: ${NEW:-NONE after 3 min}"
