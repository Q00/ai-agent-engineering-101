#!/bin/bash
# T1 (2026-11-04, replaces memory-loop.sh): runs tools/runner_v121.py, whose memory stage (memory.run under the
# memory/run.lock flock, with the LLM memory-op judge) is now the only writer of .r3/memory; restarts it 60 s after
# an exit. A STOP file in .r3_runner_v121/ keeps it down (the runner refuses to start), so a deliberate stop survives.
cd "$(dirname "$0")/.."
unset MARKET_OWNER                       # the runner never joins the market; keep the owner id out of LLM subprocesses
export RUNNER_MEMORY=1 R3_MEMORY_LLM=1 RUNNER_PERIOD_S=600 RUNNER_ICRL=1
export RUNNER_PYTHONPATH="$PWD/rlpo_v121"
mkdir -p .r3_runner_v121
while true; do
  uv run --no-sync python tools/runner_v121.py >> .r3_runner_v121/runner.out 2>&1
  rc=$?
  echo "$(date +%T) [loop] runner exited ($rc), retry in 60 s" >> .r3_runner_v121/runner.out
  sleep 60
done
