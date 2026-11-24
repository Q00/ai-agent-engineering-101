#!/bin/sh
# 최종 구간 런처 (2026-11-16 승인: 11-16 17:00~11-17 18:00). 이전 운영이 정상 종료(STOP 없음)됐을 때만 각각 시작.
D=/home/user/.local/state/ax-round3-ops; B2=/home/user/.local/state/ax-round3-buyer2
W=/home/user/student/AX/ai-agent-engineering-101-week08-v4/submissions/25512082/week-08
G=/home/user/.local/state/ax-round3-next/launch_final.log
say() { echo "$(TZ=Asia/Seoul date '+%F %T') $*" >> $G; }
say "launcher waiting (pid $$): start after 1794816030 (11-16 17:00:30 KST)"
while [ "$(date +%s)" -lt 1794816030 ]; do sleep 10; done
t0=$(date +%s)
while pgrep -f "python3 -I ops14.py" >/dev/null || pgrep -f "python3 -I buyer_ops6.py" >/dev/null || pgrep -f "market_kit.py join --dir $B2/agent" >/dev/null; do
  [ $(( $(date +%s) - t0 )) -ge 600 ] && { say "NOT STARTED: previous runs still running after 10 min"; exit 0; }
  sleep 10
done
if grep -q '"kind": "STOP"' $D/ops14.log; then say "SELLER NOT STARTED: ops14 had a safety STOP"
elif [ -e $W/restock-approval.json ] || [ -e $W/restock-approval.claimed.json ]; then say "SELLER NOT STARTED: approval file present"
elif [ -e $D/ops15_state.json ]; then say "SELLER NOT STARTED: ops15 state exists"
else (cd $D && setsid nohup python3 -I ops15.py "$(date +%s)" >> ops15.stdout 2>&1 < /dev/null &); say "SELLER STARTED ops15"; fi
if grep -q '"kind": "STOP"' $B2/buyer_ops6.log; then say "BUYER NOT STARTED: buyer_ops6 had a safety STOP"
elif [ -e $B2/buyer_ops7_state.json ]; then say "BUYER NOT STARTED: buyer_ops7 state exists"
else (cd $B2 && setsid nohup python3 -I buyer_ops7.py > buyer_ops7.stdout 2>&1 < /dev/null &); sleep 5; (cd $B2 && setsid nohup ./stop_guard7.sh >/dev/null 2>&1 < /dev/null &); say "BUYER STARTED buyer_ops7 + guard7"; fi
say "launcher done"
