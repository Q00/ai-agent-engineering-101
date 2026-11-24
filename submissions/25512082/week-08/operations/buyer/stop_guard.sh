#!/bin/sh
# 종료 감시자 (2026-11-15 03:0x 승인 반영): 최대 1,000주문 / 11-15 10:00 KST 중 먼저 오는 시점에 buyer 신규 주문 중단.
B=/home/user/.local/state/ax-round3-buyer
G=$B/stop_guard.log
say() { echo "$(TZ=Asia/Seoul date '+%F %T') $*" >> $G; }
say "guard start (pid $$): deadline 1794708000, cap 1000 orders"
stop_all() {
  say "STOP: $1"
  for p in $(pgrep -f "python3 -I buyer_ops.py"); do kill -TERM $p 2>/dev/null && say "supervisor $p SIGTERM"; done
  sleep 1
  for p in $(pgrep -f "market_kit.py join --dir $B/agent"); do kill -TERM -$(ps -o pgid= -p $p | tr -d ' ') 2>/dev/null && say "buyer kit group of $p SIGTERM"; done
  exit 0
}
while :; do
  now=$(date +%s)
  [ "$now" -ge 1794708000 ] && stop_all "deadline 11-15 10:00 KST"
  tot=$(grep -o '"orders_total": [0-9]*' $B/buyer_ops.log 2>/dev/null | tail -n 1 | grep -o '[0-9]*$')
  [ -n "$tot" ] && [ "$tot" -ge 1000 ] && stop_all "order cap 1000 reached (orders_total=$tot)"
  if ! pgrep -f "python3 -I buyer_ops.py" >/dev/null && ! pgrep -f "market_kit.py join --dir $B/agent" >/dev/null; then say "supervisor and kit already ended; guard exits"; exit 0; fi
  if [ -n "$tot" ] && [ "$tot" -ge 900 ]; then sleep 0.5; else sleep 10; fi
done
