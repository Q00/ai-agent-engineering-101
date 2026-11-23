"""Request-based hold target 5 for fast SKUs + hot list 60 -> 30 (person's decision 11/08 ~16:50; depts/request_buy.py
FAST_TARGET / FAST_H, market_db.HOT_N), on from the next restock run (no restart). 3 h later one Telegram, 3 h after vs
3 h before: request-based buys and sales, turnover, stock-out requests (tools/request_rollout.window), and the SKUs at
target 5 (at the start and at the end) with their units bought and sold in the 3 h.
    python tools/request_target_rollout.py [--report-dry]
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
from depts import request_buy as rb  # noqa: E402
from request_rollout import window  # noqa: E402

ME = "choo-chu"
H = 3
LOG = os.path.join(HERE, "logs", "request_target_rollout.log")


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def fast(db, now):
    t = rb.turnover(db, rb._state(), now)
    return {k: round(v, 2) for k, v in t.items() if rb.target(k, t) == rb.FAST_TARGET}


def report(t, f0, now=None):
    t1 = min(t + H * 3600, now or time.time())
    cat = json.load(open(os.path.join(HERE, ".market", "catalog.json"), encoding="utf-8"))
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=60)
    try:
        a, b = window(db, t, t1, cat), window(db, t - (t1 - t), t, cat)
        f1 = fast(db, t1)
        rows = []
        for sku in sorted(set(f0) | set(f1)):
            got = db.execute("SELECT COUNT(*) FROM my_stock WHERE sku=? AND supplied_at>=? AND supplied_at<?", (sku, t, t1)).fetchone()[0]
            sold = db.execute("SELECT COUNT(*) FROM settles WHERE shop=? AND sku=? AND ts>=? AND ts<?", (ME, sku, t, t1)).fetchone()[0]
            nm = ((cat.get(sku) or {}).get("name") or "")[:18]
            rows.append(f"  {sku} {nm}: 입고 {got}, 판매 {sold}, 회전 중앙값 시작 {f0.get(sku, '-')}h / 끝 {f1.get(sku, '-')}h")
    finally:
        db.close()
    return "\n".join([
        f"요청 기반 보유 목표 5/2 + hot 30 {(t1 - t) / 3600:.1f}시간 점검 ({datetime.fromtimestamp(t):%H:%M} ~ {datetime.fromtimestamp(t1):%H:%M}, 후 vs 전)",
        f"- 요청 기반 매입: {a['req_skus']}개 SKU {a['req_units']}개 vs {b['req_skus']}개 SKU {b['req_units']}개; 판매 {a['req_sold']}개 "
        f"(매출 {a['req_rev']:,}) vs {b['req_sold']}개 ({b['req_rev']:,}); 입고->판매 중앙값 {a['req_turn']} vs {b['req_turn']}시간; "
        f"24시간 무판매 제외 {a['drops']} vs {b['drops']}",
        f"- 재고 없음 요청: {a['stockouts']} vs {b['stockouts']}",
        f"- 목표 5개 SKU (시작 {len(f0)}개, 끝 {len(f1)}개):"] + (rows or ["  없음"]))


def main():
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=60)
    try:
        t = time.time()
        f0 = fast(db, t)
    finally:
        db.close()
    if "--report-dry" in sys.argv:
        print(report(t - 3600, f0))
        return
    import alerts
    import decisions
    decisions.record("purchasing", "request_target_on", "request_buy.target", {"fast": f0, "hot_n": 30},
                     "person 11/08 ~16:50: request-based SKUs with supply->sale median <= 1 h in 24 h hold 5, others 2, "
                     "3 lines a run; hot list 60 -> 30")
    alerts.send("info", "request_target_on", f"요청 기반 보유 목표 조정 + hot 60 -> 30 적용 {datetime.now():%H:%M}: 회전 1시간 이하 "
                f"SKU 목표 5개 ({len(f0)}개 SKU), 나머지 2개, run당 3줄 그대로. 3시간 점검 예정", log=log)
    log(f"on, fast {f0}")
    while time.time() < t + H * 3600 + 60:
        time.sleep(60)
    alerts.send("daily", f"request_target_report:{int(t)}", report(t, f0), log=log)
    log("report sent")


if __name__ == "__main__":
    main()
