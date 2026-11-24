#!/bin/sh
# 2026-11-17 16:2x 승인: ops15가 18:00에 "정상 종료(end)"한 경우에만 ops16(신규 매입 19:00까지, ops15 잔여 예산·수량만)을 시작한다.
# ops15가 안전 중단(stopped_final)했거나, 18:50이 지났거나, ops16 상태가 이미 있으면 시작하지 않는다. 자동 재시작 없음.
O=/home/user/.local/state/ax-round3-ops
L=/home/user/.local/state/ax-round3-next/launch_ops16.log
say() { echo "$(TZ=Asia/Seoul date '+%F %T') $*" >> $L; }
say "launcher start (pid $$)"
while pgrep -fx "python3 -I ops15.py 1794816034" >/dev/null; do sleep 15; done
LAST=$(grep -oE '"kind": "(end|stopped_final|STOP)"' $O/ops15.log | tail -n 1)
say "ops15 exited; last terminal record: $LAST"
case "$LAST" in *'"end"'*) ;; *) say "ops15 did not end normally -> no extension"; exit 0;; esac
NOW=$(date +%s); LATE=$(TZ=Asia/Seoul date -d '2026-11-17 18:50:00' +%s)
[ "$NOW" -ge "$LATE" ] && { say "too late (>=18:50) -> no extension"; exit 0; }
[ -e $O/ops16_state.json ] && { say "ops16 state exists -> no extension"; exit 0; }
cd $O && nohup setsid python3 -I ops16.py $NOW > ops16.stdout 2>&1 < /dev/null &
sleep 3; say "ops16 started: $(pgrep -fx "python3 -I ops16.py $NOW")"
