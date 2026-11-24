#!/bin/sh
# 2026-11-17 사용자 지시: 18:10 컴퓨터 종료 전 Round 3 로컬 거래 프로세스를 정상 종료한다.
# 순서: (1) 18:05까지 대기 → (2) 마지막 공급 창 정리(승인 파일 소진/미사용 기록) 확인 후 ops16·ops15·런처 SIGTERM
#       → (3) Seller 키트 로그가 30초 이상 조용해질 때까지(최대 18:08:15) 대기 → (4) run_market_locked.sh에 SIGTERM(키트·seller·터널 정리)
#       → (5) 18:09:30에 남은 프로세스는 SIGTERM만 다시 보내고 보고. SIGKILL·삭제·덮어쓰기 없음. 재시작 없음.
A=/home/user/student/AX/ai-agent-engineering-101-week08-v4/submissions/25512082/week-08
L=/home/user/.local/state/ax-round3-next/shutdown_1810.log
at() { TZ=Asia/Seoul date -d "2026-11-17 $1" +%s; }
say() { echo "$(TZ=Asia/Seoul date '+%F %T') $*" >> $L; }
pids() { pgrep -f "$1" | while read p; do [ "$p" != "$$" ] && grep -qa -- "$2" /proc/$p/cmdline 2>/dev/null && echo $p; done; }
say "shutdown script start (pid $$)"
while [ "$(date +%s)" -lt "$(at 18:05:00)" ]; do sleep 5; done
say "18:05 reached: no new supply windows will be used from now"
# (2) 공급 창 정리 대기: 승인 파일이 남아 있으면 감독의 reconcile(창 종료 60초 뒤)을 기다린다 (최대 18:07:00)
while { [ -e $A/restock-approval.json ] || [ -e $A/restock-approval.claimed.json ]; } && [ "$(date +%s)" -lt "$(at 18:07:00)" ]; do sleep 5; done
[ -e $A/restock-approval.json ] && say "WARN restock-approval.json still present at stop"
[ -e $A/restock-approval.claimed.json ] && say "WARN restock-approval.claimed.json still present at stop"
for pat in "launch_ops16.sh" "ops16.py" "ops15.py"; do
  for p in $(pgrep -f "$pat"); do
    case "$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null)" in *bash*|*pgrep*|*shutdown_1810*) continue;; esac
    kill -TERM $p 2>/dev/null && say "SIGTERM $pat pid $p"
  done
done
# (3) Seller 키트 로그가 30초 이상 조용해질 때까지 (진행 중 협상·결제 마무리), 최대 18:08:15
K=$A/logs/market-kit.log
while [ $(( $(date +%s) - $(stat -c %Y $K) )) -lt 30 ] && [ "$(date +%s)" -lt "$(at 18:08:15)" ]; do sleep 3; done
say "kit log idle $(( $(date +%s) - $(stat -c %Y $K) ))s; last line: $(tail -n 1 $K | cut -c1-160)"
# (4) 래퍼에 SIGTERM → 래퍼가 키트에 TERM 전달
for p in $(pgrep -f "run_market_locked.sh --tunnel --shop-only"); do
  case "$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null)" in *bash*|*pgrep*) continue;; esac
  kill -TERM $p 2>/dev/null && say "SIGTERM run_market_locked.sh pid $p"
done
while [ "$(date +%s)" -lt "$(at 18:09:30)" ]; do
  pgrep -f "market_kit.py join --dir $A" >/dev/null || pgrep -f "python seller.py" >/dev/null || pgrep -f "cloudflared tunnel" >/dev/null || break
  sleep 3
done
# (5) 남은 프로세스는 SIGTERM만 한 번 더 보내고 기록
for pat in "market_kit.py join --dir $A" "python seller.py" "run_agent.sh seller.py" "cloudflared tunnel" "ops16.py" "ops15.py"; do
  for p in $(pgrep -f "$pat"); do
    case "$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null)" in *bash*|*pgrep*|*shutdown_1810*) continue;; esac
    say "REMAINING $pat pid $p -> SIGTERM"; kill -TERM $p 2>/dev/null
  done
done
sleep 5
say "final process check: $(ps -eo pid,args | grep -E 'market_kit.py|seller.py|cloudflared|ops1[56].py|price_exp|buyer_ops|saved_guard|stop_guard|launch_ops16' | grep -vE 'grep|bash -c' | tr '\n' ';')"
say "shutdown script done"
