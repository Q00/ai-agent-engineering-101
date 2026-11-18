"""Hot list 30 -> 60 and request-based buying (person's decision 11/08 ~13:45; depts/request_buy.py, market_db.HOT_N),
on from the next restock run (no restart). 3 h later one Telegram, 3 h after vs 3 h before:
  request-based   SKUs and units bought (decisions 'buy' with "request-based"), cost of the units that arrived, their
                  sales and turnover (supply -> sale), drops (request_buy_drop)
  hot 31-60       buys of SKUs ranked 31-60 on the snapshot before the buy (the expansion), and their sales
  stock-outs      requests for SKUs I had no unit of (shop_subst + subst_exp no_offer)
    python tools/request_rollout.py [--report-dry]
"""
import json
import os
import re
import sqlite3
import statistics
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
ME = "choo-chu"
H = 3
LOG = os.path.join(HERE, "logs", "request_rollout.log")


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def buys(t0, t1, cat):
    """[(ts, sku, qty, request?)] from the decision log; the SKU from the line's product name."""
    by_name = {}
    for k, c in cat.items():
        by_name.setdefault((c.get("name") or "")[:40], k)
    out, drops = [], 0
    with open(os.path.join(HERE, "logs", "decisions.jsonl"), encoding="utf-8") as f:
        for ln in f:
            if '"purchasing"' not in ln:
                continue
            d = json.loads(ln)
            if not (t0 <= d["ts"] < t1):
                continue
            if d.get("action") == "request_buy_drop":
                drops += 1
            if d.get("action") != "buy":
                continue
            r = d.get("reason") or ""
            m = re.match(r"\[(?:low|high)\] (.+?): ", r)
            sku = by_name.get(m.group(1)[:40]) if m else None
            q = re.search(r"buy (\d+)", str(d.get("value")))
            if sku:
                out.append((d["ts"], sku, int(q.group(1)) if q else 1, "request-based" in r))
    return out, drops


def rank_at(db, ts, sku):
    row = db.execute("SELECT MAX(ts) FROM snapshots WHERE ts<=?", (ts,)).fetchone()
    if not row or not row[0]:
        return None
    snap = db.execute("SELECT sku, sold_5m, sold_30m FROM snapshots WHERE ts=?", (row[0],)).fetchall()
    order = [k for k, a, b in sorted(snap, key=lambda r: (r[1] or 0, r[2] or 0), reverse=True)]
    return order.index(sku) + 1 if sku in order else None


def stockout_requests(db, t0, t1):
    n = db.execute("SELECT COUNT(*) FROM shop_subst WHERE ts>=? AND ts<?", (t0, t1)).fetchone()[0]
    try:
        with open(os.path.join(HERE, "logs", "subst_exp.jsonl"), encoding="utf-8") as f:
            n += sum(1 for ln in f if '"no_offer"' in ln and t0 <= json.loads(ln)["ts"] < t1)
    except OSError:
        pass
    return n


def window(db, t0, t1, cat):
    b, drops = buys(t0, t1, cat)
    req = {sku for _, sku, _, r in b if r}
    exp = set()
    for ts, sku, q, r in b:
        if not r:
            rk = rank_at(db, ts, sku)
            if rk and 30 < rk <= 60:
                exp.add(sku)

    def sales(skus):
        if not skus:
            return 0, 0, None, 0
        q = ",".join("?" * len(skus))
        arrived = db.execute(f"SELECT unit, cost, supplied_at FROM my_stock WHERE sku IN ({q}) AND supplied_at>=? AND "
                             f"supplied_at<?", (*skus, t0, t1)).fetchall()
        s = db.execute(f"SELECT s.amount, s.ts - m.supplied_at FROM settles s JOIN my_stock m ON m.unit=s.unit WHERE "
                       f"s.shop=? AND s.sku IN ({q}) AND s.ts>=? AND s.ts<? AND m.supplied_at>=?",
                       (ME, *skus, t0, t1, t0)).fetchall()
        turn = [x[1] / 3600 for x in s if x[1] is not None]
        return len(s), sum(x[0] for x in s), (round(statistics.median(turn), 2) if turn else None), sum(c or 0 for _, c, _ in arrived)
    rs, es = sales(req), sales(exp)
    return {"req_skus": len(req), "req_units": sum(q for _, s, q, r in b if r), "req_sold": rs[0], "req_rev": rs[1],
            "req_turn": rs[2], "req_cost": rs[3], "exp_skus": len(exp),
            "exp_units": sum(q for _, s, q, r in b if not r and s in exp), "exp_sold": es[0], "exp_rev": es[1],
            "stockouts": stockout_requests(db, t0, t1), "drops": drops}


def report(t, now=None):
    t1 = min(t + H * 3600, now or time.time())
    cat = json.load(open(os.path.join(HERE, ".market", "catalog.json"), encoding="utf-8"))
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=60)
    try:
        a, b = window(db, t, t1, cat), window(db, t - (t1 - t), t, cat)
    finally:
        db.close()
    return "\n".join([
        f"hot 60 + 요청 기반 매입 {(t1 - t) / 3600:.1f}시간 점검 ({datetime.fromtimestamp(t):%H:%M} ~ {datetime.fromtimestamp(t1):%H:%M}, 후 vs 전)",
        f"- 요청 기반: {a['req_skus']}개 SKU {a['req_units']}개 매입 (입고 원가 {a['req_cost']:,}), 그중 판매 {a['req_sold']}개 "
        f"매출 {a['req_rev']:,}, 입고->판매 중앙값 {a['req_turn']}시간; 24시간 무판매로 제외 {a['drops']}개",
        f"- hot 31~60위로 새로 들어온 SKU: {a['exp_skus']}개 {a['exp_units']}개 매입, 판매 {a['exp_sold']}개 매출 {a['exp_rev']:,} "
        f"(전: {b['exp_skus']}개 SKU가 31~60위에서 매입됨 - 확장 전에는 이 구간은 forum wanted만)",
        f"- 재고 없음 요청 {a['stockouts']} vs {b['stockouts']}"])


def main():
    if "--report-dry" in sys.argv:
        print(report(time.time() - 3600))
        return
    import alerts
    import decisions
    t = time.time()
    decisions.record("purchasing", "request_buy_on", "hot/request", {"hot_n": 60, "min_requests": 50, "target": 2},
                     "person 11/08 ~13:45: hot list 30 -> 60, request-based candidates (>= 50 stock-out requests in 24 h, "
                     "basic filters, 2 held, food >= 72 h, at most 3 lines a run), dropped after 24 h without a sale")
    alerts.send("info", "request_buy_on", f"hot 매입 대상 30 -> 60, 요청 기반 매입 적용 {datetime.now():%H:%M} (24시간 재고 없음 "
                "요청 50건 이상 + 기본 필터, SKU당 2개, 식품 72시간 이상, run당 최대 3줄, 24시간 무판매면 제외). 3시간 점검 예정", log=log)
    while time.time() < t + H * 3600 + 60:
        time.sleep(60)
    alerts.send("daily", f"request_report:{int(t)}", report(t), log=log)
    log("report sent")


if __name__ == "__main__":
    main()
