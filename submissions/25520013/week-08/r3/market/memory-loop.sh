#!/bin/bash
# Runs the shadow memory layers (memory.run: graph, gotchas, prices) every 60 s; restarts them 60 s after an exit.
cd "$(dirname "$0")/.."
while true; do
  uv run --no-sync python -m memory.run --state .market --loop 60 >> .market/memory.out 2>&1
  rc=$?
  echo "$(date +%T) [loop] memory runner exited ($rc), retry in 60 s" >> .market/memory.out
  sleep 60
done
