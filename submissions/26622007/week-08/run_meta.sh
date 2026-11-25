#!/usr/bin/env bash
# Keeps the meta-harness running next to run_market.sh on the Mac mini.
#
#   nohup submissions/26622007/week-08/run_meta.sh > /dev/null 2>&1 &
#   kill $(cat submissions/26622007/week-08/.market/run_meta.pid)
#
# Settings from the environment or the secrets file: META_TERRA_MODEL, META_ASTRA_MODEL, META_PUSH=1 to push after Astra.
set -u
export PATH="$HOME/.local/bin:$PATH"     # the market account's own codex
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
ENV_FILE="$HERE/../.env"
read_env() {
  grep -E "^\s*(export\s+)?$1=" "$ENV_FILE" | tail -1 | sed -E "s/^\s*(export\s+)?$1=//; s/^[\"']//; s/[\"']\s*$//"
}
export SHOP_MARKET_NAME="${MARKET_NAME:-nocha12}"
for k in META_TERRA_MODEL META_ASTRA_MODEL META_PUSH; do
  v="$(read_env "$k")"; [ -n "$v" ] && export "$k=$v"
done
mkdir -p "$HERE/.market"
source "$HERE/lib_single.sh"
wait_turn "$HERE/.market/run_meta.pid" run_meta.sh
source "$ROOT/.venv/bin/activate"
cd "$HERE"
while true; do
  python meta_harness.py run
  echo "$(date '+%m-%d %H:%M:%S') [run_meta] exited with $?, restarting in 60 s" >> "$HERE/logs/meta.log"
  sleep 60
done
