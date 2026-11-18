"""Sofa warranty by grade (person's decision 11/09 ~11:10): C grade sofa units are sold without a warranty, A / B keep
72 h (depts/sofa.warranty_for, used by depts/sales.warranty_hours in the seller process), so the seller restarts once,
after the 14:00 high-price 3 h report of tools/high40_rollout.py ("report sent" in logs/high40_rollout.log; at the
latest 14:20). The buy exclusion of G5e3784e6 (buy_exclude.json) and the refund-count fix (alerts.py) need no restart.
    python tools/sofa_grade_rollout.py          (log in logs/sofa_grade_rollout.log)
"""
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)

LOG = os.path.join(HERE, "logs", "sofa_grade_rollout.log")
AFTER = os.path.join(HERE, "logs", "high40_rollout.log")
LATEST = datetime(2026, 11, 9, 14, 20).timestamp()


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def report_sent():
    try:
        with open(AFTER, encoding="utf-8") as f:
            return any("report sent" in ln for ln in f)
    except OSError:
        return False


def main():
    import alerts
    import decisions
    from seller_conc_rollout import restart_seller, seller_back
    log("waiting for the 14:00 high-price report")
    while not report_sent() and time.time() < LATEST:
        time.sleep(20)
    t = time.time()
    killed = restart_seller()
    back = None
    for _ in range(36):
        time.sleep(5)
        back = seller_back(t)
        if back:
            break
    log(f"seller restart: killed {killed}, back {back}")
    decisions.record("cs", "seller_restart", "sofa.warranty_for", bool(back),
                     "person 11/09 ~11:10: C grade sofas without a warranty, A / B 72 h")
    alerts.send("info" if back else "urgent", "sofa_grade_on",
                f"소파 보증 등급 규칙 적용 {datetime.now():%H:%M}: C등급 소파 보증 없음, A/B 72시간 유지. "
                f"seller 재시작 {'완료' if back else '확인 안 됨 - 확인 필요'}", log=log)


if __name__ == "__main__":
    main()
