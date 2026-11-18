"""Seller restart at 11/08 21:05, after the substitute experiment ends at 21:00 (person's decision 11/08 ~13:30): the
offer parse that skips numbers with a unit ("540 hours", "1 unit"; depts/receipt_check.first_number, on from 21:05)
goes in with it. One Telegram when the seller is back (or not back within 3 min).
Also notes that c8a62c7 (same message as d04a766 by mistake) added tools/aov_rollout.py.
    python tools/restart_2105.py        (log in logs/restart_2105.log)
"""
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
AT = datetime(2026, 11, 8, 21, 5).timestamp()
LOG = os.path.join(HERE, "logs", "restart_2105.log")


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def main():
    log("waiting for 21:05")
    while time.time() < AT:
        time.sleep(30)
    import alerts
    import decisions
    from seller_conc_rollout import restart_seller, seller_back
    t = time.time()
    killed = restart_seller()
    back = None
    for _ in range(36):
        time.sleep(5)
        back = seller_back(t)
        if back:
            break
    log(f"killed {killed}, back {back}")
    decisions.record("sales", "offer_parse_units_on", "receipt_check.first_number", None,
                     "person 11/08 ~13:30: numbers with a unit are not offers; seller restarted 21:05 "
                     + ("ok" if back else "(not back yet)"))
    if back:
        alerts.send("info", "restart_2105", "21:05 seller 재시작 정상: 시간·수량 숫자(540 hours, 1 unit 등)를 제안가로 읽지 않음", log=log)
    else:
        alerts.send("urgent", "restart_2105_back", "21:05 seller 재시작 후 3분 안에 복귀 줄이 안 보임. 확인 필요", log=log)


if __name__ == "__main__":
    main()
