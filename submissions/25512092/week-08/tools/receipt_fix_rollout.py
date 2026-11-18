"""Seller restart for the receipt check (depts/receipt_check.py, person's decision 11/08 ~11:20), after the 14:03
3 h check of STOCK_CAP 20M (person: restart only after it). At AT: restart the seller, wait for it to come back,
one Telegram; 3 h later: the receipt_fixed decisions so far (issued at the agreed price / countered).
    python tools/receipt_fix_rollout.py        (log in logs/receipt_fix_rollout.log)
"""
import json
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
import alerts  # noqa: E402
import decisions  # noqa: E402
from seller_conc_rollout import restart_seller, seller_back  # noqa: E402

LOG = os.path.join(HERE, "logs", "receipt_fix_rollout.log")
AT = datetime(2026, 11, 8, 14, 6).timestamp()


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def fixed(t0):
    out = {"receipt": 0, "counter": 0}
    with open(os.path.join(HERE, "logs", "decisions.jsonl"), encoding="utf-8") as f:
        for ln in f:
            if '"receipt_fixed"' in ln:
                d = json.loads(ln)
                if d["ts"] >= t0:
                    out["counter" if "no receipt" in (d.get("reason") or "") else "receipt"] += 1
    return out


def main():
    log("waiting for 14:06 (after the 14:03 check)")
    while time.time() < AT:
        time.sleep(30)
    t = time.time()
    killed = restart_seller()
    back = None
    for _ in range(36):
        time.sleep(5)
        back = seller_back(t)
        if back:
            break
    log(f"seller restarted (killed {killed}), back: {back}")
    decisions.record("sales", "receipt_check_on", "receipt_check", None,
                     f"person 11/08 ~11:20: receipt amount checked by code, seller restarted {'ok' if back else '(not back yet)'}")
    if back:
        alerts.send("info", "receipt_check_on", f"영수증 금액 코드 검증 적용 {datetime.now():%H:%M} (seller 재시작 정상): 합의가와 "
                    "다르면 합의가로 발행, 합의가가 하한 미만이면 영수증 없이 재호가, decision receipt_fixed", log=log)
    else:
        alerts.send("urgent", "receipt_check_back", "영수증 검증 적용용 seller 재시작 후 3분 안에 복귀 줄이 안 보임. 확인 필요", log=log)
    while time.time() < t + 3 * 3600:
        time.sleep(60)
    f = fixed(t)
    alerts.send("daily", "receipt_check_3h", f"영수증 검증 3시간: receipt_fixed {f['receipt'] + f['counter']}건 "
                f"(합의가로 발행 {f['receipt']}, 하한 미만이라 재호가 {f['counter']})", log=log)
    log(f"3 h: {f}")


if __name__ == "__main__":
    main()
