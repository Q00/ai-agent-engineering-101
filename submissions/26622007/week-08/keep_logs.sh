#!/usr/bin/env bash
# Keeps every part of the kit's log for the submission. The kit (scripts/market_kit.py, read only) moves
# logs/market-kit.log to market-kit.log.1 at 20 MB and replaces the previous .log.1 the next time, so a two-week
# Round 3 would lose its early settlement lines. Every few minutes this saves a new market-kit.log.1, gzipped and
# otherwise unchanged, as logs/kit-parts/market-kit-<rotated at>-<sha256 prefix>.log.gz.
#
#   nohup submissions/26622007/week-08/keep_logs.sh > /dev/null 2>&1 &
#   kill $(cat submissions/26622007/week-08/.market/keep_logs.pid)
#
# KEEP_LOGS_EVERY (seconds, default 300) and KEEP_LOGS_DIR (the week-08 folder, for tests) can be set.
set -u
HERE="${KEEP_LOGS_DIR:-$(cd "$(dirname "$0")" && pwd)}"
EVERY="${KEEP_LOGS_EVERY:-300}"
PART="$HERE/logs/market-kit.log.1"
OUT="$HERE/logs/kit-parts"

mkdir -p "$OUT" "$HERE/.market"
echo $$ > "$HERE/.market/keep_logs.pid"
note() { echo "$(date '+%m-%d %H:%M:%S') [keep_logs] $1" >> "$HERE/logs/keep_logs.log"; }
note "watching $PART every ${EVERY}s"

while true; do
  if [ -s "$PART" ]; then
    sum="$(shasum -a 256 "$PART" | cut -c1-16)"
    if ! ls "$OUT"/*-"$sum".log.gz > /dev/null 2>&1; then
      dest="$OUT/market-kit-$(date -r "$PART" '+%m%d-%H%M%S')-$sum.log.gz"
      if gzip -c "$PART" > "$dest.tmp" && [ "$(gzip -dc "$dest.tmp" | shasum -a 256 | cut -c1-16)" = "$sum" ]; then
        mv "$dest.tmp" "$dest"
        note "saved $(wc -c < "$PART" | tr -d ' ') bytes as ${dest#$HERE/}"
      else
        rm -f "$dest.tmp"
        note "could not save $PART (it changed while copying?); trying again next time"
      fi
    fi
  fi
  sleep "$EVERY"
done
