#!/usr/bin/env bash
# Keeps the shop and buyer in the market until the deadline: the kit exits on a
# network error (it did on 10-20, a DNS failure), so this starts it again.
# Run from the repository root with MARKET_OWNER=<claim code> in the environment.
cd "$(dirname "$0")/../../.." || exit 1
while true; do
  python scripts/market_kit.py join --dir submissions/25512081/week-07 \
      --market https://market.ourolabs.ai --name kimauve --tunnel
  echo "$(date '+%m-%d %H:%M:%S') [keepalive] kit exited ($?); restarting in 20s"
  sleep 20
done
