#!/bin/sh
set -eu

agent_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH= cd -- "$agent_dir/../../.." && pwd)
lock_file="$agent_dir/.market/market-kit.lock"
kit_pid_file="$agent_dir/.market/kit.pid"

: "${MARKET_OWNER:?MARKET_OWNER must be set from the existing protected claim file}"
: "${OWN_AGENT_NAME:=sohyunkim11}"
export OWN_AGENT_NAME

# Keep this supervisor alive as the lock owner. With `flock <file> command`,
# killing the flock wrapper can orphan command. An exec can also close an
# inherited descriptor, so this shell holds the fd and forwards termination to
# its one market_kit child.
exec 9>"$lock_file"
if ! flock -n 9; then
  echo "market kit is already running for $agent_dir" >&2
  exit 73
fi

child_pid=
clear_child_pid() {
  if [ -n "$child_pid" ] && [ -f "$kit_pid_file" ] && [ "$(cat "$kit_pid_file" 2>/dev/null)" = "$child_pid" ]; then
    rm -f "$kit_pid_file"
  fi
}
stop_child() {
  trap - HUP INT TERM
  if [ -n "$child_pid" ] && kill -0 "$child_pid" 2>/dev/null; then
    kill -TERM "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
  clear_child_pid
}
trap 'stop_child; exit 143' HUP INT TERM

"$repo_dir/.venv/bin/python" "$repo_dir/scripts/market_kit.py" join \
  --dir "$agent_dir" \
  --market https://market.ourolabs.ai \
  --name sohyunkim11 \
  "$@" &
child_pid=$!
printf '%s\n' "$child_pid" >"$kit_pid_file"
status=0
wait "$child_pid" || status=$?
clear_child_pid
child_pid=
exit "$status"
