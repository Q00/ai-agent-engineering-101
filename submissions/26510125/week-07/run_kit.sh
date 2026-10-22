#!/usr/bin/env bash
# Week 07 — keep the market kit running on this machine until the deadline.
#
#   bash run_kit.sh            # foreground, restarts the kit if it exits
#   nohup setsid bash run_kit.sh >/dev/null 2>&1 &     # detached: survives the terminal / IDE session
#
# What it does: joins https://market.ourolabs.ai as shop + buyer "YS-SHOP" through a cloudflared
# quick tunnel (the kit proxies the seller; the public URL changes on every restart, the name and
# the key stay the same so trades and reviews carry over). The claim code that links the agents to
# the GitHub account is read from ~/.market_owner (outside the repository) when that file exists.
# Everything the kit does is in logs/market-kit.log; this loop's own notes go to logs/run_kit.log.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
cd "$HERE"

export PATH="/opt/conda/bin:$HOME/.local/bin:$PATH"           # python 3.10 with a2a-sdk; cloudflared (http2 wrapper)
export CLAUDE_BIN="${CLAUDE_BIN:-$(ls -d "$HOME"/.vscode-server/extensions/anthropic.claude-code-*/resources/native-binary/claude 2>/dev/null | tail -1)}"
unset ANTHROPIC_API_KEY OPENAI_API_KEY OPENAI_BASE_URL         # the harness is claude -p on the subscription
[ -f "$HOME/.market_owner" ] && export MARKET_OWNER="$(tr -d '[:space:]' < "$HOME/.market_owner")"

MARKET="${MARKET_URL:-https://market.ourolabs.ai}"
NAME="${MARKET_NAME:-YS-SHOP}"
mkdir -p logs
while true; do
  echo "$(date '+%F %T') [run_kit] starting market_kit join as $NAME (owner: ${MARKET_OWNER:+set}${MARKET_OWNER:-not set})" >> logs/run_kit.log
  python "$REPO/scripts/market_kit.py" join --dir "$HERE" --market "$MARKET" --name "$NAME" --tunnel \
    ${MARKET_OWNER:+--owner "$MARKET_OWNER"} >> logs/run_kit.log 2>&1
  echo "$(date '+%F %T') [run_kit] kit exited with $?; restarting in 30 s" >> logs/run_kit.log
  sleep 30
done
