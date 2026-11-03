#!/usr/bin/env bash
# Keeps nocha12's shop and buyer in the market on this machine, restarting the kit if it stops.
#
#   nohup submissions/26622007/week-08/run_market.sh > /dev/null 2>&1 &
#   kill $(cat submissions/26622007/week-08/.market/run_market.pid)     # leave the market
#
# Secrets come from submissions/26622007/.env (gitignored) and only these two are exported:
#   OPENROUTER_API_KEY   the shop's and the buyer's harness (Luna on OpenRouter, HARNESS in agent.json)
#   MARKET_OWNER         the claim code that links the agents to @Nocha12
# ANTHROPIC_API_KEY in the same file is left out on purpose: with it, a `claude -p` harness
# would bill the API instead of using the Claude Code login.
set -u
export PATH="$HOME/.local/bin:$PATH"     # the market account's own cloudflared, codex and uv
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
ENV_FILE="$HERE/../.env"
NAME="${MARKET_NAME:-nocha12}"

read_env() {
  grep -E "^\s*(export\s+)?$1=" "$ENV_FILE" | tail -1 | sed -E "s/^\s*(export\s+)?$1=//; s/^[\"']//; s/[\"']\s*$//"
}

export OPENROUTER_API_KEY="$(read_env OPENROUTER_API_KEY)"
export MARKET_OWNER="$(read_env MARKET_OWNER)"
export SHOP_MARKET_NAME="$NAME"
[ -n "$OPENROUTER_API_KEY" ] || { echo "OPENROUTER_API_KEY missing in $ENV_FILE" >&2; exit 1; }
[ -n "$MARKET_OWNER" ] || { echo "MARKET_OWNER missing in $ENV_FILE" >&2; exit 1; }

mkdir -p "$HERE/.market"
source "$HERE/lib_single.sh"
wait_turn "$HERE/.market/run_market.pid" run_market.sh    # launchd at boot, or by hand: one copy at a time
caffeinate -ims -w $$ &            # no sleep while this loop runs
CAF=$!

# A background job in a script ignores SIGINT, so the kit gets TERM. Its children are collected
# first: the cloudflared tunnel outlives a terminated kit. Waiting on everything would also wait
# on caffeinate, which waits on this script.
KIT=""
leave() {
  if [ -n "$KIT" ]; then
    CHILDREN="$(pgrep -P "$KIT")"
    kill -TERM "$KIT" 2>/dev/null
    wait "$KIT"
    [ -n "$CHILDREN" ] && kill $CHILDREN 2>/dev/null
  fi
  kill "$CAF" 2>/dev/null
  exit 0
}
trap leave INT TERM

cd "$ROOT"
source .venv/bin/activate
while true; do
  # The kit prints why it stops (e.g. a refused join) to stderr only; keep it next to the kit's log.
  python scripts/market_kit.py join --dir "$HERE" --market https://market.ourolabs.ai --name "$NAME" --tunnel \
    2>> "$HERE/logs/kit-stderr.log" &
  KIT=$!
  wait "$KIT"
  rc=$?                           # read before $(date) below, which would overwrite $?
  echo "$(date '+%H:%M:%S') [run_market] kit exited with $rc, restarting in 10 s" >> "$HERE/logs/market-kit.log"
  sleep 10
done
