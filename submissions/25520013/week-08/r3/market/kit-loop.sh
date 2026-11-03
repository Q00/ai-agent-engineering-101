#!/bin/bash
# Keeps the shop and the buyer in the market: runs the kit with --tunnel; when it exits, waits 5 min and runs it again.
cd "$(dirname "$0")/.."
while true; do
  echo "$(date +%T) [loop] starting kit (shop and buyer)" >> .market/join.out
  uv run python ../../../scripts/market_kit.py join --dir . --market https://market.ourolabs.ai --name lee-sungsoo --tunnel >> .market/join.out 2>&1
  echo "$(date +%T) [loop] kit exited ($?), retry in 300 s" >> .market/join.out
  sleep 300
done
