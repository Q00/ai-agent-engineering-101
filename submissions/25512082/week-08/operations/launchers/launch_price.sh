#!/bin/sh
# ops15가 실제로 시작된 경우에만 가격 실험 제어를 시작 (최대 17:20까지 대기)
D=/home/user/.local/state/ax-round3-ops; G=/home/user/.local/state/ax-round3-next/launch_final.log
say() { echo "$(TZ=Asia/Seoul date '+%F %T') $*" >> $G; }
while [ "$(date +%s)" -lt 1794817200 ]; do
  if [ -e $D/ops15_state.json ] && pgrep -f "python3 -I ops15.py" >/dev/null; then
    (cd $D && setsid nohup python3 -I price_exp.py >> price_exp.stdout 2>&1 < /dev/null &); say "PRICE EXPERIMENT STARTED"; exit 0; fi
  sleep 15
done
say "PRICE EXPERIMENT NOT STARTED: ops15 not running by 17:20"
