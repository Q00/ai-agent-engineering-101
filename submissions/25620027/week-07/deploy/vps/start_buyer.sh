#!/bin/sh
set -eu
umask 077
exec 9>/data/buyer-runtime.lock
flock -n 9
test -r /run/private/buyer_key.pem
test -r /run/private/market.env
test -f /data/paid-quota.sqlite3
test -f /run/kit/market_kit.py
stop_epoch=$(date -u -d '2026-10-28T08:15:00Z' +%s)
run_seconds=$((stop_epoch - $(date -u +%s)))
test "$run_seconds" -gt 0
remaining_orders=$(python -c 'import json, urllib.request; r=urllib.request.Request("https://market.ourolabs.ai/api/board", headers={"User-Agent":"AX7-public-research"}); b=json.load(urllib.request.urlopen(r,timeout=20)); assert b["round"]==2; p=next(x for x in b["buyers"] if x["name"]=="25620027"); assert p["owner"]["login"]=="SungJinho"; print(max(0,1200-p["orders"]))')
test "$remaining_orders" -gt 0
mkdir -p /data/buyer/logs /data/buyer/.market
cp /app/deploy/vps/buyer-agent.json /data/buyer/agent.json
ln -sfn /run/private/buyer_key.pem /data/buyer/buyer_key.pem
ln -sfn /run/private/shop_key.pem /data/buyer/shop_key.pem
printf 'BUYER_START orders_remaining=%s deadline=2026-10-28T17:15+09:00\n' "$remaining_orders"
exec timeout --signal=TERM --kill-after=15 "$run_seconds" \
  uv run --offline --no-project --no-sync \
  --env-file /run/private/openai.env \
  --env-file /run/private/openrouter.env \
  --env-file /run/private/market.env \
  python /run/kit/market_kit.py join \
  --market https://market.ourolabs.ai --name 25620027 \
  --dir /data/buyer --buyer-only --deployed https://ax-week07.treasuresky.xyz \
  --orders "$remaining_orders"
