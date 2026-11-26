#!/usr/bin/env bash
# Join Round 3 from this folder with the week-07 name and keys (copy shop_key.pem/buyer_key.pem here first).
#   ./run_kit.sh --background     detach; log in logs/kit.out, pid in kit.pid
# The claim code is read from ~/.market_owner (never committed).
HERE="$(cd "$(dirname "$0")" && pwd)"
export PATH="/nas/home/lys0426/agent-market/bin:$HOME/.local/bin:/opt/conda/bin:$PATH"
unset ANTHROPIC_API_KEY
export MARKET_OWNER="${MARKET_OWNER:-$(cat ~/.market_owner)}"
CMD=(/nas/home/lys0426/agent-market/.venv/bin/python "$HERE/market_kit.py" join --dir "$HERE" --market https://market.ourolabs.ai --name yeonsong2 "${MODE:---tunnel}")
if [ "${1:-}" = --background ]; then
  mkdir -p "$HERE/logs"
  setsid nohup "${CMD[@]}" > "$HERE/logs/kit.out" 2>&1 < /dev/null &
  echo $! > "$HERE/kit.pid"; echo "kit started (pid $(cat "$HERE/kit.pid"))"
else
  exec "${CMD[@]}"
fi
