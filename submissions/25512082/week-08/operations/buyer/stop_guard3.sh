#!/bin/sh
# 종료 감시자 #2: 11-16 10:00 KST(1794790800) 또는 3,000주문 도달 시 buyer 감독과 Kit 종료 (감독 내부 종료 기능의 이중 안전장치)
B=/home/user/.local/state/ax-round3-buyer2
G=$B/stop_guard3.log
say() { echo "$(TZ=Asia/Seoul date '+%F %T') $*" >> $G; }
say "guard3 start (pid $$): deadline 1794790800, cap 2900"
stop_all() { say "STOP: $1"; for p in $(pgrep -f "python3 -I buyer_ops3.py"); do kill -TERM $p && say "supervisor $p SIGTERM"; done; sleep 1; for p in $(pgrep -f "market_kit.py join --dir $B/agent"); do kill -TERM -$(ps -o pgid= -p $p | tr -d ' ') 2>/dev/null && say "kit group of $p SIGTERM"; done; exit 0; }
while :; do
  [ "$(date +%s)" -ge 1794790800 ] && stop_all "deadline 11-16 10:00 KST"
  tot=$(grep -o '"orders_total": [0-9]*' $B/buyer_ops3.log 2>/dev/null | tail -n 1 | grep -o '[0-9]*$')
  [ -n "$tot" ] && [ "$tot" -ge 2900 ] && stop_all "cap 2900 (orders_total=$tot)"
  if [ -f $B/buyer_ops3.log ] && ! pgrep -f "python3 -I buyer_ops3.py" >/dev/null && ! pgrep -f "market_kit.py join --dir $B/agent" >/dev/null; then say "supervisor and kit ended; guard exits"; exit 0; fi
  sleep 10
done
