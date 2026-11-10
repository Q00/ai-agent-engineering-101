#!/usr/bin/env bash
# Keeps the shop and buyer in the market until the deadline: the kit exits on a
# network error (it did on 10-20, a DNS failure), so this starts it again. It waits
# until the market answers before joining, so a market outage does not open a new
# Cloudflare tunnel every few seconds (too many get refused with 429).
# Run from the repository root with MARKET_OWNER=<claim code> in the environment.
cd "$(dirname "$0")/../../.." || exit 1
MARKET=https://market.ourolabs.ai
while true; do
  until [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$MARKET/api/board")" = 200 ]; do
    echo "$(date '+%m-%d %H:%M:%S') [keepalive] market not answering; checking again in 30s"
    sleep 30
  done
  python scripts/market_kit.py join --dir submissions/25512081/week-07 \
      --market "$MARKET" --name kimauve --tunnel
  echo "$(date '+%m-%d %H:%M:%S') [keepalive] kit exited ($?); restarting in 30s"
  sleep 30
done
