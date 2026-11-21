#!/bin/bash
# Round 3 on the lab server: the kit (shop + buyer, --tunnel) and the side processes, each restarted when it dies.
#   kit            scripts/market_kit.py join --dir week-08 (seller, restock hook, market_buyer, review)
#   collector      the market into data/market.db (every estimator reads it)
#   manager        the seller's hourly review          forum         the shop's forum replies and status posts
#   buyer_manager  the buyer's hourly review           buyer_forum   the buyer's posts of its purchases
# The kit is also restarted when the shop's public card fails 3 checks in a row; a failed start (Cloudflare 429)
# waits 180 s. Every restart goes to Telegram (notify.py), at most once per process per 10 minutes.
# Run inside tmux:  tmux new -d -s r3 ~/agent-market/submissions/25520014/week-08/run_market.sh
ROOT=$HOME/agent-market
DIR=$ROOT/submissions/25520014/week-08
OUT=$ROOT/kit-stdout.txt
cd $ROOT
export PATH="$ROOT/.venv/bin:$HOME/.local/bin:$PATH" VIRTUAL_ENV="$ROOT/.venv"
export MARKET_OWNER=$(cat $ROOT/.market_owner)
mkdir -p $DIR/logs
# one copy only: a previous run's kit and side processes (e.g. after tmux kill-session) go first, ours go with us
pkill -u $(id -u) -f "market_kit.py join --dir $DIR" ; pkill -u $(id -u) -f "\.py --dir $DIR" ; sleep 2
trap 'kill $(jobs -p) 2>/dev/null; exit' EXIT HUP INT TERM
log() { echo "$(date +%F' '%T) $*" | tee -a $ROOT/watchdog.log; }
declare -A last_note
note() {                                  # $1 process, $2 text; Telegram at most once per process per 10 min
  local now=$(date +%s)
  if (( now - ${last_note[$1]:-0} >= 600 )); then
    last_note[$1]=$now
    (cd $DIR && python notify.py "[watchdog] $2" > /dev/null 2>&1 &)
  fi
}

SIDE=(collector manager forum buyer_manager buyer_forum)
declare -A spid
start_side() {                            # every side process takes the agent folder (its .market/ is the kit's)
  (cd $DIR && exec python -u $1.py --dir $DIR >> $DIR/logs/$1.txt 2>&1) &
  spid[$1]=$!
}

pid="" fails=0
while true; do
  if [[ -z $pid ]] || ! kill -0 $pid 2>/dev/null; then
    [[ -n $pid ]] && { log "kit exited"; note kit "kit exited, restarting"; }
    # keep every run's output (market-kit.log rotates and loses its oldest part; this is the full record)
    [[ -s $OUT ]] && mv $OUT $ROOT/kit-stdout.$(date +%m%d-%H%M%S).txt
    log "starting kit"
    python -u scripts/market_kit.py join --dir $DIR \
        --market https://market.ourolabs.ai --name inextro --tunnel > $OUT 2>&1 &
    pid=$! fails=0
    while kill -0 $pid 2>/dev/null && ! grep -q "buyer registered" $OUT; do sleep 3; done
    if grep -q "buyer registered" $OUT; then
      log "registered, kit pid $pid, $(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' $OUT | head -1)"
    else
      log "start failed: $(tail -1 $OUT)"; note kit "kit start failed: $(tail -1 $OUT | cut -c1-200)"
      pid=""; sleep 180; continue
    fi
  fi
  for s in "${SIDE[@]}"; do
    if [[ -z ${spid[$s]} ]] || ! kill -0 ${spid[$s]} 2>/dev/null; then
      if [[ -n ${spid[$s]} ]]; then log "$s exited, restarting"; note $s "$s exited, restarted"; fi
      start_side $s
      log "$s started, pid ${spid[$s]}"
    fi
  done
  sleep 60
  # our URL now: the kit's own lines only (buyer lines name other shops' tunnels), the newest after a re-tunnel
  url=$(grep -E '^[0-9:]+ \[tunnel\]|registered again' $OUT | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1)
  if curl -sf -m 15 "$url/.well-known/agent-card.json" > /dev/null; then
    fails=0
  else
    fails=$((fails + 1)); log "card check failed ($fails/3) at $url"
    if (( fails >= 3 )); then log "restarting kit"; note kit "shop card unreachable 3 times, kit restarted"
      kill $pid; sleep 5; pid=""; fi
  fi
done
