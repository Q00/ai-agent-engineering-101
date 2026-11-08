#!/usr/bin/env bash
# server-53, not part of the submission: crontab runs this every 2 hours. One headless Claude Code run reads
# OVERSEER.md, checks the market agent, fixes what the logs justify, commits in ~/market-agent/repo, and writes a
# report to ~/market-agent/overseer/. usage_guard.py pauses at the configured subscription limits. flock keeps two runs from overlapping; timeout caps one run at 30 minutes.
exec 9>"$HOME/market-agent/overseer.lock"
flock -n 9 || exit 0
export TZ=Asia/Seoul
export PATH=$HOME/market-agent/.venv/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
export PYTHONPATH=$HOME/market-agent/clockfix
mkdir -p "$HOME/market-agent/overseer"
STAMP=$(python -c 'import time; print(time.strftime("%m%d-%H%M"))')
REPORT="$HOME/market-agent/overseer/$STAMP.md"
cd "$HOME/market-agent/repo/submissions/25512081/week-08" || exit 1
PROMPT="Read $HOME/market-agent/OVERSEER.md first and follow it exactly. Then do one run of the overseer checklist for the agent in this folder. Write your report to $REPORT: start with at most 3 plain Korean sentences (state, what you changed or why nothing, what the user must do if anything), then the numbers you measured and every command result that justified a change. If you changed code, the report names the commit hashes."
timeout 1800 python usage_guard.py --report "$REPORT" -- \
  claude -p "$PROMPT" --model opus --max-turns 60 \
  --allowedTools "Bash,Read,Edit,Write,Grep,Glob" --permission-mode acceptEdits \
  --add-dir "$HOME/market-agent" --output-format json \
  > "$HOME/market-agent/overseer/$STAMP.json" 2> "$HOME/market-agent/overseer/$STAMP.err"
python - "$HOME/market-agent/overseer/$STAMP.json" >> "$HOME/market-agent/overseer/runs.jsonl" <<'PY'
import json, sys, time
try:
    d = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    d = {}
print(json.dumps({"ts": time.strftime("%m-%d %H:%M"), "cost_usd": d.get("total_cost_usd"), "turns": d.get("num_turns"),
                  "is_error": d.get("is_error"), "paused": d.get("paused", False), "quota": d.get("quota"), "result": (d.get("result") or "")[:300]}, ensure_ascii=False))
PY
