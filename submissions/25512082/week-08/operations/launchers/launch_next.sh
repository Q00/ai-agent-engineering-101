#!/bin/sh
# 다음 구간 런처 (2026-11-15 승인: 11-16 10:00~17:00). 이전 운영이 정상 종료(안전 중단 기록 없음)됐을 때만 각각 시작.
D=/home/user/.local/state/ax-round3-ops; B2=/home/user/.local/state/ax-round3-buyer2
W=/home/user/student/AX/ai-agent-engineering-101-week08-v4/submissions/25512082/week-08
G=/home/user/.local/state/ax-round3-next/launch_next.log
say() { echo "$(TZ=Asia/Seoul date '+%F %T') $*" >> $G; }
say "launcher waiting (pid $$): start after 1794790830 (11-16 10:00:30 KST)"
while [ "$(date +%s)" -lt 1794790830 ]; do sleep 20; done
# 이전 프로세스 종료 대기 (최대 10분). 겹치면 시작하지 않음.
t0=$(date +%s)
while pgrep -f "python3 -I ops13.py" >/dev/null || pgrep -f "python3 -I buyer_ops4.py" >/dev/null || pgrep -f "market_kit.py join --dir $B2/agent" >/dev/null; do
  [ $(( $(date +%s) - t0 )) -ge 600 ] && { say "NOT STARTED: previous runs still running after 10 min"; exit 0; }
  sleep 10
done
# Seller
if grep -q '"kind": "STOP"' $D/ops13.log; then say "SELLER NOT STARTED: ops13 had a safety STOP"
elif [ -e $W/restock-approval.json ] || [ -e $W/restock-approval.claimed.json ]; then say "SELLER NOT STARTED: approval file present"
elif [ -e $D/ops14_state.json ]; then say "SELLER NOT STARTED: ops14 state exists"
else (cd $D && setsid nohup python3 -I ops14.py "$(date +%s)" >> ops14.stdout 2>&1 < /dev/null &); say "SELLER STARTED ops14"; fi
# Buyer
if grep -q '"kind": "STOP"' $B2/buyer_ops4.log; then say "BUYER NOT STARTED: buyer_ops4 had a safety STOP"
elif [ -e $B2/buyer_ops5_state.json ]; then say "BUYER NOT STARTED: buyer_ops5 state exists"
else (cd $B2 && setsid nohup python3 -I buyer_ops5.py > buyer_ops5.stdout 2>&1 < /dev/null &); sleep 5; (cd $B2 && setsid nohup ./stop_guard5.sh >/dev/null 2>&1 < /dev/null &); say "BUYER STARTED buyer_ops5 + guard5"; fi
say "launcher done"
