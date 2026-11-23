"""High-price floor max(cost x 1.10, market median x 0.80) (person's decision 11/08 ~17:30; depts/high_ask.py
floor_rule, factors in high_ask.json), switched on right after the 20:02 report of tools/high_revert.py ("report sent"
in logs/high_revert.log; at the latest 20:15), with a seller restart (the floor is read in the seller process).
The first-ask cap at the market average stays. 3 h later one Telegram, 3 h after vs 3 h before: high-price
negotiations, receipts, sales, profit over cost of high-price sales, high-price stock at cost and units.
    python tools/floor_rollout.py [--report-dry]          (log in logs/floor_rollout.log)
"""
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
from depts import high_ask  # noqa: E402
from high_revert import window  # noqa: E402

LOG = os.path.join(HERE, "logs", "floor_rollout.log")
AFTER = os.path.join(HERE, "logs", "high_revert.log")
LATEST = datetime(2026, 11, 8, 20, 15).timestamp()
H = 3


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def report_sent():
    try:
        with open(AFTER, encoding="utf-8") as f:
            return any("report sent" in ln for ln in f)
    except OSError:
        return False


def report(t, now=None):
    t1 = min(t + H * 3600, now or time.time())
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=60)
    try:
        a, b = window(db, t, t1), window(db, t - (t1 - t), t)
    finally:
        db.close()
    fr = high_ask.rules().get("floor_rule") or {}
    m = lambda x: f"{x['hi_profit'] / (x['hi_rev'] - x['hi_profit']):+.1%}" if x["hi_rev"] - x["hi_profit"] else "-"
    return "\n".join([
        f"고가 하한 max(원가 x {fr.get('cost')}, 중앙값 x {fr.get('median')}) {(t1 - t) / 3600:.1f}시간 점검 "
        f"({datetime.fromtimestamp(t):%H:%M} ~ {datetime.fromtimestamp(t1):%H:%M}, 후 vs 전)",
        f"- 고가 협상 {a['neg']} vs {b['neg']}, 영수증 {a['rec']} vs {b['rec']}, 판매 {a['hi_n']} vs {b['hi_n']}",
        f"- 고가 판매 이익(원가 대비) {a['hi_profit']:,} ({m(a)}) vs {b['hi_profit']:,} ({m(b)}); 매출 {a['hi_rev']:,} vs {b['hi_rev']:,}",
        f"- 고가 재고(끝 시점) 원가 {a['held_cost']:,} {a['held_n']}개 vs {b['held_cost']:,} {b['held_n']}개"])


def main():
    if "--report-dry" in sys.argv:
        print(report(time.time() - 3600))
        return
    import alerts
    import decisions
    from seller_conc_rollout import restart_seller, seller_back
    log("waiting for the 20:02 report")
    while not report_sent() and time.time() < LATEST:
        time.sleep(20)
    r = high_ask.rules()
    fr = r.setdefault("floor_rule", {})
    fr.update(enabled=True, since=round(time.time()))
    with open(high_ask.RULES, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False)
    t = time.time()
    killed = restart_seller()
    back = None
    for _ in range(36):
        time.sleep(5)
        back = seller_back(t)
        if back:
            break
    log(f"floor rule on {fr}; seller restart: killed {killed}, back {back}")
    decisions.record("sales", "high_floor_on", "high_ask.floor_rule", fr,
                     f"person 11/08 ~17:30: high-price floor max(cost x {fr.get('cost')}, median x {fr.get('median')}); "
                     f"seller back {bool(back)}")
    alerts.send("info" if back else "urgent", "high_floor_on",
                f"고가 하한 max(원가 x {fr.get('cost')}, 시장 중앙값 x {fr.get('median')}) 적용 {datetime.now():%H:%M}, "
                f"첫 호가 평균 이하 규칙 유지. seller 재시작 {'완료' if back else '확인 안 됨 - 확인 필요'}. 3시간 점검 예정", log=log)
    while time.time() < t + H * 3600 + 60:
        time.sleep(60)
    alerts.send("daily", f"high_floor_report:{int(t)}", report(t), log=log)
    log("report sent")


if __name__ == "__main__":
    main()
