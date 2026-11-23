"""High-price buy exclusion by our own cost (person's decision 11/09 ~23:40; criteria 11/09 ~23:50). A high-price SKU
(unit cost > qc.HIGH_MIN) is not bought as a high-price line when, over the last WINDOW_H (48 h):
  cost  we bought at least MIN_N (3) units (market.db my_stock) and their average cost is over RATIO (95%) of its
        market average (market_db.sku_avg, now), or
  loss  I sold at least MIN_SOLD (3; person 11/09 ~23:55) and the profit of those sales (amount - cost) adds up below 0
(depts/purchasing.make_plan, the sofa trial), plus
the SKUs in "force" (G8095ddd6, 에이스 BMA 1139-E: the market average is under our cost). Read every restock run from
high_cost_exclude.json; the latest list in logs/high_cost_exclude.json (one line in the 08:00 summary).
"""
import json
import os
import sqlite3
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "high_cost_exclude.json")
OUT = os.path.join(HERE, "logs", "high_cost_exclude.json")
DEFAULT = {"enabled": False, "ratio": 0.95, "window_h": 48, "min_n": 3, "min_sold": 3, "force": []}
ME = "choo-chu"


def rules():
    try:
        with open(RULES, encoding="utf-8") as f:
            return dict(DEFAULT, **json.load(f))
    except (OSError, ValueError):
        return dict(DEFAULT)


def compute(now=None, r=None, db_path=None):
    """{sku: {"cost", "avg", "ratio", "n", "name", "why"}}"""
    from depts.qc import HIGH_MIN
    from market_db import sku_avg
    now = now or time.time()
    r = rules() if r is None else r
    if not r.get("enabled"):
        return {}
    db = sqlite3.connect(db_path or os.path.join(HERE, "market.db"), timeout=30)
    out = {}
    try:
        rows = db.execute("SELECT sku, MAX(name), AVG(cost), COUNT(*) FROM my_stock WHERE supplied_at>=? AND cost>? "
                          "GROUP BY sku", (now - r["window_h"] * 3600, HIGH_MIN)).fetchall()
        for sku, name, cost, n in rows:
            if n < r.get("min_n", 3):
                continue
            avg, _ = sku_avg(db, sku, now)
            if avg and cost > avg * r["ratio"]:
                out[sku] = {"cost": round(cost), "avg": round(avg), "ratio": round(cost / avg, 3), "n": n,
                            "name": name, "why": "cost"}
        for sku, name, profit, n in db.execute(
                "SELECT m.sku, MAX(m.name), SUM(s.amount - m.cost), COUNT(*) FROM settles s JOIN my_stock m ON "
                "m.unit=s.unit WHERE s.shop=? AND s.ts>=? AND m.cost>? GROUP BY m.sku", (ME, now - r["window_h"] * 3600,
                                                                                      HIGH_MIN)):
            if profit < 0 and n >= r.get("min_sold", 3) and sku not in out:
                out[sku] = {"profit": round(profit), "sold": n, "name": name, "why": "loss"}
        for sku in r.get("force") or []:
            if sku not in out:
                row = db.execute("SELECT MAX(name) FROM my_stock WHERE sku=?", (sku,)).fetchone()
                out[sku] = {"name": row[0] if row else None, "why": "force"}
    finally:
        db.close()
    return out


def current(now=None, log=None):
    """compute() and write it to OUT (for the summary); {} on any failure (buying goes on unchanged)."""
    try:
        ex = compute(now)
    except Exception as e:
        if log:
            log(f"[high_cost_exclude] failed: {type(e).__name__}: {e}")
        return {}
    try:
        old = json.load(open(OUT, encoding="utf-8")).get("skus", {})
    except (OSError, ValueError):
        old = {}
    if set(old) != set(ex) and log:
        log(f"[high_cost_exclude] {len(ex)} SKUs: +{sorted(set(ex) - set(old))} -{sorted(set(old) - set(ex))}")
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"ts": round(now or time.time()), "skus": ex}, f, ensure_ascii=False)
    os.replace(tmp, OUT)
    return ex


def line():
    """One line for the 08:00 summary."""
    try:
        j = json.load(open(OUT, encoding="utf-8"))
    except (OSError, ValueError):
        return "고가 매입 제외(high_cost_exclude): 목록 없음 (아직 계산 안 됨)"
    sk = j.get("skus") or {}
    r = rules()
    tag = lambda v: (f" 원가 {v['ratio']:.0%}" if v.get("why") == "cost" else f" 손실 {v['profit']:,}" if v.get("why") == "loss"
                     else " 고정")
    items = [f"{(v.get('name') or k)[:14]} {k}{tag(v)}" for k, v in sk.items()]
    return (f"고가 매입 제외(최근 {r['window_h']}시간: 매입 {r.get('min_n', 3)}개+ 원가 > 시장 평균 {r['ratio']:.0%}, 또는 "
            f"판매 {r.get('min_sold', 3)}개+ 이익 합 < 0) {len(sk)}개"
            + (": " + ", ".join(items[:12]) + (" 외" if len(items) > 12 else "") if items else ""))
