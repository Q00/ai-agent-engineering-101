#!/usr/bin/env bash
# Joins the market with kit v4 in the background (Linux version of join.ps1). The claim code is never stored here:
#   MARKET_OWNER=mk-... bash submissions/25512192/week-08/join.sh
# Leave: kill $(cat submissions/25512192/week-08/kit.pid)
set -e
dir="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$dir/../../.." && pwd)"
if [ -z "$MARKET_OWNER" ]; then echo "set MARKET_OWNER to the claim code from https://market.ourolabs.ai/join" >&2; exit 1; fi
cd "$repo"
[ -x "$repo/.venv/bin/python3" ] && export PATH="$repo/.venv/bin:$PATH"   # a2a-sdk, uvicorn, httpx live in the repo venv
nohup python3 scripts/market_kit.py join --dir "$dir" --market https://market.ourolabs.ai --name winun1127 --tunnel \
  >"$dir/join.out" 2>"$dir/join.err" </dev/null &
echo $! >"$dir/kit.pid"
echo "kit started, pid $!; log: $dir/logs/market-kit.log"
