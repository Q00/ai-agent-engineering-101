"""24 h after the receipt hold change (15 min -> p99 rule, 3 min; the 11/08 14:06 seller restart), one Telegram:
the 24 h after vs the 24 h before (person's request 11/08 ~13:00), read-only:
  held units        receipts never paid (no settle of the unit at that amount within 30 min): each held a unit
  missed requests   requests naming that SKU while it was held (shop_subst: "not in stock", substitutes offered)
  late_pay_conflict decisions (after); the same rule replayed with the 15-min hold (before)
  1-star stock reviews  my_results stars 1 whose text is a stock reason (depts/review_link.reason)
    python tools/receipt_hold_report.py [--dry]
"""
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
T = datetime(2026, 11, 8, 14, 6).timestamp()
ME = "choo-chu"


def window(db, t0, t1, hold):
    from depts import receipt_hold
    sku_of = dict(db.execute("SELECT task, sku FROM shop_deals WHERE outcome='receipt' AND ts>=?", (t0 - 900,)).fetchall())
    rows = [(sku_of.get(task), unit, price, rts) for unit, recs in receipt_hold._receipts(db, t0).items()
            for rts, task, price in recs if t0 <= rts < t1]
    held = missed = 0
    for sku, unit, price, rts in rows:
        if not rts or db.execute("SELECT 1 FROM settles WHERE shop=? AND unit=? AND amount=? AND ts BETWEEN ? AND ?",
                                 (ME, unit, price, rts - 5, rts + 1800)).fetchone():
            continue
        held += 1
        missed += db.execute("SELECT COUNT(*) FROM shop_subst WHERE x=? AND ts BETWEEN ? AND ?",
                             (sku, rts, rts + hold)).fetchone()[0]
    from depts import review_link
    one = sum(1 for (r,) in db.execute("SELECT review FROM my_results WHERE stars=1 AND ts>=? AND ts<?", (t0, t1))
              if review_link.reason(r) == "stock")
    return {"receipts": len(rows), "held": held, "missed": missed, "stock_1star": one}


def text(t1):
    from depts import receipt_hold
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=60)
    try:
        h = receipt_hold.hold_s()
        a, b = window(db, T, t1, h), window(db, T - (t1 - T), T, 900)
        before_conf = len(receipt_hold.conflicts(db, T - (t1 - T), T, hold=900))
    finally:
        db.close()
    after_conf = 0
    with open(os.path.join(HERE, "logs", "decisions.jsonl"), encoding="utf-8") as f:
        for ln in f:
            if '"late_pay_conflict"' in ln and json.loads(ln)["ts"] >= T:
                after_conf += 1
    return "\n".join([
        f"재고 묶음 시간 변경 {(t1 - T) / 3600:.0f}시간 결과 (15분 -> {h / 60:.0f}분, {datetime.fromtimestamp(T):%m-%d %H:%M} 이후 vs 이전 같은 길이)",
        f"- 미결제로 묶인 단위 {a['held']} vs {b['held']} (영수증 {a['receipts']} vs {b['receipts']})",
        f"- 묶인 동안 놓친 요청(같은 SKU, 재고 없음 -> 대체 제안) {a['missed']} vs {b['missed']}",
        f"- late_pay_conflict {after_conf} vs {before_conf} (이전은 15분 묶음으로 같은 규칙 재계산)",
        f"- 재고 관련 1점 리뷰 {a['stock_1star']} vs {b['stock_1star']}"])


def main():
    if "--dry" in sys.argv:
        print(text(time.time()))
        return
    while time.time() < T + 86400 + 300:
        time.sleep(60)
    import alerts
    alerts.send("daily", "receipt_hold_report:20261109", text(T + 86400))


if __name__ == "__main__":
    main()
