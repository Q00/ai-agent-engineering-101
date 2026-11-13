#!/usr/bin/env bash
# Keeps nocha12's shop and buyer in the market on this machine, restarting the kit if it stops.
#
#   nohup submissions/26622007/week-07/run_market.sh > /dev/null 2>&1 &
#   kill $(cat submissions/26622007/week-07/.market/run_market.pid)     # leave the market
#
# Secrets come from submissions/26622007/.env (gitignored) and only these two are exported:
#   OPENROUTER_API_KEY   the buyer's harness (Luna on OpenRouter)
#   MARKET_OWNER         the claim code that links the agents to @Nocha12
# ANTHROPIC_API_KEY in the same file is left out on purpose: with it, the seller's `claude -p`
# would bill the API instead of using the Claude Code login.
set -u
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
echo $$ > "$HERE/.market/run_market.pid"
caffeinate -dimsu -w $$ &          # no sleep while this loop runs

KIT=""
trap '[ -n "$KIT" ] && kill -INT "$KIT" 2>/dev/null; wait; exit 0' INT TERM

cd "$ROOT"
source .venv/bin/activate
while true; do
  python scripts/market_kit.py join --dir "$HERE" --market https://market.ourolabs.ai --name "$NAME" --tunnel &
  KIT=$!
  wait "$KIT"
  echo "$(date '+%H:%M:%S') [run_market] kit exited with $?, restarting in 10 s" >> "$HERE/logs/market-kit.log"
  sleep 10
done
