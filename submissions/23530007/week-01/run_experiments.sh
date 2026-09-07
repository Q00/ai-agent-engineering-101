#!/usr/bin/env bash
# Capture the four comparison runs described in TOOLS.md. Run from this directory.
set -u
mkdir -p logs
DROP_CLOCK=1        python first_agent.py 2>&1 | tee logs/run-01-two-tools.txt
CLOCK_DESC=terse    python first_agent.py 2>&1 | tee logs/run-02-terse.txt
                    python first_agent.py 2>&1 | tee logs/run-03-full.txt
python first_agent.py "Read notes.txt and sum the numbers in it." 2>&1 | tee logs/run-04-sum-only.txt
