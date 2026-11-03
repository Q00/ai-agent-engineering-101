#!/usr/bin/env bash
# Week 9 SETUP: which of the Round 3 opening lines logs/market-kit.log already shows, in the lecture's order.
#
#   bash tools/round3_setup_check.sh            one line per check, then "setup: N/6"
#   ROUND3_CHECK_DIR=<week-08> for tests
#
# Read only. Exit 0 when all six are there, 1 otherwise (2 when there is no log).
set -u
HERE="${ROUND3_CHECK_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
LOG="$HERE/logs/market-kit.log"
[ -s "$LOG" ] || { echo "no $LOG yet"; exit 2; }
ok=0
check() {   # label, extended regex
  local hit
  hit="$(grep -E -m1 -- "$2" "$LOG")"
  if [ -n "$hit" ]; then ok=$((ok + 1)); echo "ok   $1: ${hit:0:160}"; else echo "--   $1: not yet"; fi
}
check "kit v4"          '\[kit\] market_kit 4'
check "Round 3 shop"    '\[market\] shop registered as .*, round ([3-9]|[1-9][0-9])'
check "supply"          '\[supply\] [0-9]+ x '
check "shop settled"    '\[acp\] settled'
check "buyer paid"      '\[acp\] paid'
check "forum"           '\[forum\] (post|vote) as '
echo "setup: $ok/6"
[ "$ok" -eq 6 ]
