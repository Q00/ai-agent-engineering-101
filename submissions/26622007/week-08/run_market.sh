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

select_join_dir() {
  local store="$1" selector="$1/current-join" directory path owner mode canonical
  if [ ! -e "$selector" ] && [ ! -L "$selector" ]; then
    printf '%s\n' "$HERE"
    return 0
  fi
  [ -L "$selector" ] || { echo "[join-selector] symlink required" >&2; return 1; }
  directory="$(cd "$selector" 2>/dev/null && pwd -P)" || return 1
  case "$directory" in "$store"/join-generations/*) ;; *) return 1 ;; esac
  canonical="${directory#"$store"/join-generations/}"
  case "$canonical" in ""|*/*|.*|*$'\n'*|*$'\r'*) return 1 ;; esac
  [ -f "$directory/agent.json" ] && [ ! -L "$directory/agent.json" ] || return 1
  for path in "$store" "$store/join-generations" "$directory" "$directory/agent.json"; do
    [ ! -L "$path" ] || return 1
    owner="$(/usr/bin/stat -f %u "$path")" || return 1
    mode="$(/usr/bin/stat -f %Lp "$path")" || return 1
    [ "$owner" = 501 ] && (( (8#$mode & 022) == 0 )) || return 1
  done
  mode="$(/usr/bin/stat -f %Lp "$directory/agent.json")" || return 1
  (( (8#$mode & 0222) == 0 )) || return 1
  mode="$(/usr/bin/stat -f %Lp "$directory")" || return 1
  (( (8#$mode & 0222) == 0 )) || return 1
  canonical="$(cd "$store" 2>/dev/null && pwd -P)" || return 1
  [ "$canonical" = "$store" ] || return 1
  if [ "$(/usr/bin/id -u)" != 501 ]; then
    for path in "$store" "$store/join-generations" "$directory" "$directory/agent.json"; do
      [ ! -w "$path" ] || return 1
    done
  fi
  printf '%s\n' "$directory"
}

if [ "${1:-}" = --check-join-selector ]; then
  select_join_dir "${2:-/Users/Shared/ax-buyer-W-20261106-1950}"
  exit $?
fi

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
  JOIN_DIR="$(select_join_dir /Users/Shared/ax-buyer-W-20261106-1950)" || {
    echo "[join-selector] invalid managed generation; kit not started" >&2
    exit 1
  }
  # The kit prints why it stops (e.g. a refused join) to stderr only; keep it next to the kit's log.
  python scripts/market_kit.py join --dir "$JOIN_DIR" --market https://market.ourolabs.ai --name "$NAME" --tunnel \
    2>> "$HERE/logs/kit-stderr.log" &
  KIT=$!
  wait "$KIT"
  rc=$?                           # read before $(date) below, which would overwrite $?
  echo "$(date '+%H:%M:%S') [run_market] kit exited with $rc, restarting in 10 s" >> "$HERE/logs/market-kit.log"
  sleep 10
done
