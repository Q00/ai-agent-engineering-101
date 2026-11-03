#!/bin/bash
# Runs the forum poster every 60 s (it posts at most once per forum.interval_s); restarts it 60 s after an exit.
cd "$(dirname "$0")/.."
while true; do
  uv run --no-sync python hooks/forum.py --state .market --loop 60 >> .market/forum.out 2>&1
  echo "$(date +%T) [loop] forum poster exited ($?), retry in 60 s" >> .market/forum.out
  sleep 60
done
