#!/usr/bin/env bash
# Restart the kit when the market no longer lists my shop (11-11 15:37: the tunnel answered from here, the kit was
# up, but /api/shops had dropped jiseokkk and the board showed alive=false; buyers stopped getting the shop in their
# lists and opens fell from ~90/h to 30/h). Two misses in a row -> rejoin (new tunnel, same name and keys).
# cron: */10 * * * * /home/iai4/Desktop/han/ai-agent-engineering-101/submissions/26510129/week-08/watchdog.sh >> /home/iai4/Desktop/han/ai-agent-engineering-101/submissions/26510129/week-08/logs/watchdog.log 2>&1
ROOT=/home/iai4/Desktop/han/agent-market
STATE=/tmp/kit-watchdog-misses
NAME=jiseokkk
now=$(date "+%Y-%m-%d %H:%M:%S")
listed=$(curl -s -m 15 https://market.ourolabs.ai/api/shops | python3 -c "import sys,json; print(sum(1 for s in json.load(sys.stdin)['shops'] if s.get('name')=='$NAME'))" 2>/dev/null)
alive=$(ps -p "$(cat $ROOT/kit.pid 2>/dev/null)" >/dev/null 2>&1 && echo yes || echo no)
if [ "$listed" = "1" ] && [ "$alive" = "yes" ]; then
  echo "$now ok" ; echo 0 > $STATE; exit 0
fi
misses=$(( $(cat $STATE 2>/dev/null || echo 0) + 1 )); echo $misses > $STATE
echo "$now miss $misses (listed=$listed kit=$alive)"
if [ "$misses" -ge 2 ] || [ "$alive" = "no" ]; then
  echo "$now restarting the kit"
  kill "$(cat $ROOT/kit.pid 2>/dev/null)" 2>/dev/null; sleep 3
  $ROOT/join.sh --background
  echo 0 > $STATE
fi
