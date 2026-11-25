#!/usr/bin/env bash
# Starts run_market.sh, run_meta.sh and keep_logs.sh if they are not running. For the market account's crontab on
# the Mac mini, where launchd agents are out of reach (another account holds the screen, and there is no sudo):
#
#   @reboot     /bin/bash <this folder>/ensure_running.sh
#   */5 * * * * /bin/bash <this folder>/ensure_running.sh
#
# A script counts as running when the pid in its .market/<name>.pid is alive and its command names the script
# (lib_single.sh writes that file), so a running copy is never started twice.
set -u
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export LANG="${LANG:-en_US.UTF-8}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
mkdir -p "$HERE/logs"

running() {   # running <pid file> <script name>
  local pid
  pid="$(cat "$1" 2>/dev/null)" && [ -n "$pid" ] && ps -p "$pid" -o command= 2>/dev/null | grep -q "$2"
}

cd "$ROOT"
for s in run_market.sh run_meta.sh keep_logs.sh; do
  # .market/<name>.disabled keeps a script off on purpose (e.g. the owner turned the meta-harness off).
  [ -e "$HERE/.market/${s%.sh}.disabled" ] && continue
  if ! running "$HERE/.market/${s%.sh}.pid" "$s"; then
    nohup /bin/bash "$HERE/$s" > /dev/null 2>&1 < /dev/null &
    echo "$(date '+%m-%d %H:%M:%S') [ensure_running] $s was not running; started it as pid $!" >> "$HERE/logs/ensure_running.log"
  fi
done
