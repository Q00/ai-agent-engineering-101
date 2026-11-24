#!/bin/sh
set -eu

agent_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
pid_file="$agent_dir/.market/shop-only.pid"
export PATH="/home/user/.local/bin:$PATH"
export MARKET_OWNER="$(cat /home/user/.config/ax-week07/market-owner)"
export OWN_AGENT_NAME="sohyunkim11"

mkdir -p "$agent_dir/.market"
printf '%s\n' "$$" >"$pid_file"

child_pid=
stopping=0
cleanup() {
  trap - EXIT HUP INT TERM
  stopping=1
  if [ -n "$child_pid" ] && kill -0 "$child_pid" 2>/dev/null; then
    kill -TERM "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
  if [ -f "$pid_file" ] && [ "$(cat "$pid_file" 2>/dev/null)" = "$$" ]; then
    rm -f "$pid_file"
  fi
}
trap cleanup EXIT HUP INT TERM

attempt=0
while [ "$stopping" -eq 0 ]; do
  attempt=$((attempt + 1))
  "$agent_dir/run_market_locked.sh" --tunnel --shop-only &
  child_pid=$!
  status=0
  wait "$child_pid" || status=$?
  child_pid=
  [ "$stopping" -ne 0 ] && break
  printf '%s\n' "[shop-supervisor] market kit exited with status $status; retry $attempt in 5 s" >&2
  sleep 5
done
