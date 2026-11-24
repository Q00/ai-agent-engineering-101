#!/bin/sh
# Launcher v2 (user approval 2026-11-14 ~22:40 KST): start ops11 at 11-15 03:50:27 KST under the NEW approval.
# ops10 stays stopped and its STOP record is kept; ops11 uses its own new state file.
D=/home/user/.local/state/ax-round3-ops
A=/home/user/student/AX/ai-agent-engineering-101-week08-v4/submissions/25512082/week-08
L=$D/launch11.log
say() { echo "$(TZ=Asia/Seoul date '+%F %T') $*" >> $L; }
say "launcher v2 waiting (pid $$)"
while [ "$(date +%s)" -lt 1794685827 ]; do sleep 20; done
if pgrep -f "ops10.py" >/dev/null; then say "NOT STARTED: ops10 still running"; exit 0; fi
for f in restock-approval.json restock-approval.claimed.json; do
  if [ -e "$A/$f" ]; then say "NOT STARTED: $f present"; exit 0; fi
done
if [ -e $D/ops11_state.json ]; then say "NOT STARTED: ops11 state already exists"; exit 0; fi
if [ "$(date +%s)" -ge 1794708000 ]; then say "NOT STARTED: past 10:00"; exit 0; fi
say "conditions met -> starting ops11 (new approval, new state)"
cd $D && exec python3 -I ops11.py "$(date +%s)" >> $D/ops11.stdout 2>&1
