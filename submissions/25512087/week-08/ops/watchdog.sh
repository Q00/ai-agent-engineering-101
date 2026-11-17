#!/usr/bin/env bash
# Market kit watchdog (week 08, 11/17). launchd runs it every 2 minutes (ai.ggari.market-watchdog.plist).
# If the kit log has not moved for STALE_S seconds, or no kit process is running, and the market is reachable,
# it runs restart.sh --no-test. A cooldown keeps it from restarting in a loop while the network is flaky.
#   bash watchdog.sh            one check (what launchd runs)
#   bash watchdog.sh --status   print what it would do, change nothing
set -u
ROOT='/Users/simgyuseong/AXtransformation/agent-market'
SUB='/Users/simgyuseong/AXtransformation/ai-agent-engineering-101/submissions/25512087/week-08'
LOG="$SUB/logs/market-kit.log"
STATE="$ROOT/.watchdog-last-restart"
LOCK="$ROOT/.watchdog.lock"
STALE_S=600          # kit log older than 10 min = the kit is stuck or gone
COOLDOWN_S=900       # at most one restart per 15 min
MARKET='https://market.ourolabs.ai/rules'

now=$(date +%s)
ts() { date '+%m-%d %H:%M:%S'; }

mtime=$(stat -f %m "$LOG" 2>/dev/null || stat -c %Y "$LOG" 2>/dev/null || echo 0)
age=$((now - mtime))
kits=$(pgrep -f "market_kit.py join" | wc -l | tr -d ' ')
last=$(cat "$STATE" 2>/dev/null || echo 0)
since=$((now - last))
if /usr/bin/curl -sS -m 10 -o /dev/null "$MARKET" 2>/dev/null; then online=1; else online=0; fi

reason=""
[ "$kits" -eq 0 ] && reason="no kit process"
[ "$age" -gt "$STALE_S" ] && reason="${reason:+$reason, }kit log idle ${age}s"

if [ "${1:-}" = "--status" ]; then
  echo "log age ${age}s, kits ${kits}, online ${online}, last restart ${since}s ago, reason: ${reason:-none}"
  exit 0
fi

[ -z "$reason" ] && exit 0                                   # healthy: say nothing
if [ "$online" -eq 0 ]; then echo "$(ts) [watchdog] $reason, but the market is unreachable: waiting"; exit 0; fi
if [ "$since" -lt "$COOLDOWN_S" ]; then echo "$(ts) [watchdog] $reason, restarted ${since}s ago: waiting"; exit 0; fi
if ! mkdir "$LOCK" 2>/dev/null; then echo "$(ts) [watchdog] another check is running"; exit 0; fi
trap 'rmdir "$LOCK"' EXIT

echo "$now" > "$STATE"
echo "$(ts) [watchdog] $reason -> restart.sh --no-test"
/bin/bash "$ROOT/restart.sh" --no-test 2>&1 | sed "s/^/$(ts) [restart] /"
