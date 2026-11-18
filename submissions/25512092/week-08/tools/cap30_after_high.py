"""STOCK_CAP 20M -> 30M again (person's decision 11/09 ~11:20), right after the 14:00 high-price 3 h report of
tools/high40_rollout.py ("report sent" in logs/high40_rollout.log; at the latest 14:20). Same steps as
tools/cap30_rollout.py (analyst path with the b0 check, brake low 20M with its three conditions, 3 h report); the
proposal row it writes is checked by the analyst 3 h after, against the market (ceo_policy.worse(market=...)).
    python tools/cap30_after_high.py          (log in logs/cap30_rollout.log)
"""
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))
import cap30_rollout as c  # noqa: E402

AFTER = os.path.join(HERE, "logs", "high40_rollout.log")
LATEST = datetime(2026, 11, 9, 14, 20).timestamp()


def report_sent():
    try:
        with open(AFTER, encoding="utf-8") as f:
            return any("report sent" in ln for ln in f)
    except OSError:
        return False


if __name__ == "__main__":
    c.WHEN, c.EXTRA = "11/09 ~11:20", ""
    c.NOTE = ("[주의] 14:00 seller 재시작에 오래 열린 협상 자동 해제(잠금 해제)가 같이 적용됨 (C등급 소파 보증, B등급 보증 "
              "시험도): 매출·회전 변화에 그 효과가 섞여 있음")
    c.WHY = "person's decision 11/09 ~11:20: STOCK_CAP 20M -> 30M again after the 14:00 report, brake back to 20M"
    c.log("11/09: waiting for the 14:00 high-price report")
    while "--now" not in sys.argv and not report_sent() and time.time() < LATEST:   # 11/09 ~12:05: --now
        time.sleep(20)
    c.main()
