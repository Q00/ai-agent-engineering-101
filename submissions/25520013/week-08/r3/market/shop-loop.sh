#!/bin/bash
# Keeps the shop in the market: runs the kit with --shop-only --tunnel; when it exits, waits 5 min and runs it again.
cd "$(dirname "$0")/.."
while true; do
  echo "$(date +%T) [loop] starting kit (shop only)" >> .market/join-shop.out
  uv run python ../../../scripts/market_kit.py join --dir . --market https://market.ourolabs.ai --name lee-sungsoo --shop-only --tunnel >> .market/join-shop.out 2>&1
  echo "$(date +%T) [loop] kit exited ($?), retry in 300 s" >> .market/join-shop.out
  sleep 300
done
