"""11/08 11:05: the extra numbers for the 11:00 experiment reports (person's decision 11/07 ~20:20), read-only, one
Telegram. Window: 11/07 11:00 -> 11/08 11:00 (both experiments went on with the 11/07 11:00 seller restart).
  grade claim  per arm (claim / none) x grade (A / B / C, the unit's grade): negotiations, deal rate (receipts),
               average sale price, price / first ask, stars (the buyer's review within 15 min after the settle)
  code haggle  per arm (code / harness) x price band (first ask < 10k, 10-100k, 100-500k, >= 500k): negotiations,
               deal rate, margin on receipts ((price - cost) / cost), average sale price, price / first ask, LLM calls
               (shop_haggle.harness_calls: total and per negotiation; after 20:05 the code arm hands buyer questions
               to the LLM, so it can have calls). Sofas (own rules) left out.
    python tools/report_1100_extra.py [--dry]     (waits until AT; --dry: the window so far, printed, not sent)
"""
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)

T0 = datetime(2026, 11, 7, 11, 0).timestamp()
T1 = datetime(2026, 11, 8, 11, 0).timestamp()
AT = datetime(2026, 11, 8, 11, 5).timestamp()
ME = "choo-chu"
BANDS = [("<10k", 0, 10_000), ("10k-100k", 10_000, 100_000), ("100k-500k", 100_000, 500_000), (">=500k", 500_000, 1e12)]


def _avg(v, nd=0):
    return (int(round(sum(v) / len(v))) if nd == 0 else round(sum(v) / len(v), nd)) if v else None


def claim_by_grade(db, t0, t1):
    rows = db.execute("SELECT r.claim_arm, r.unit, d.outcome, d.price, d.first_ask, m.grade FROM shop_receipt r "
                      "LEFT JOIN shop_deals d ON d.task=r.task LEFT JOIN my_stock m ON m.unit=r.unit "
                      "WHERE r.claim_arm IN ('claim','none') AND r.ts>=? AND r.ts<?", (t0, t1)).fetchall()
    g = defaultdict(lambda: {"n": 0, "rc": 0, "price": [], "ratio": [], "stars": []})
    for arm, unit, out, price, ask, grade in rows:
        a = g[(arm, grade or "?")]
        a["n"] += 1
        if out == "receipt" and price:
            a["rc"] += 1
            a["price"].append(price)
            if ask:
                a["ratio"].append(price / ask)
            s = db.execute("SELECT buyer, ts FROM settles WHERE shop=? AND unit=? AND amount=? AND ts>=?",
                           (ME, unit, price, t0)).fetchone()
            if s:
                st = db.execute("SELECT stars FROM my_results WHERE buyer=? AND ts BETWEEN ? AND ? ORDER BY ts LIMIT 1",
                                (s[0], s[1], s[1] + 900)).fetchone()
                if st and st[0] is not None:
                    a["stars"].append(st[0])
    return g


def haggle_by_band(db, t0, t1):
    rows = db.execute("SELECT h.haggle, h.harness_calls, d.outcome, d.price, d.first_ask, d.cost FROM shop_haggle h "
                      "JOIN shop_deals d ON d.task=h.task WHERE h.ts>=? AND h.ts<? AND h.haggle IN ('code','harness') "
                      "AND d.outcome IN ('receipt','cancelled','rejected') AND d.first_ask IS NOT NULL "
                      "AND COALESCE(h.rule,'')!='sofa'", (t0, t1)).fetchall()
    g = defaultdict(lambda: {"n": 0, "rc": 0, "margin": [], "price": [], "ratio": [], "calls": 0})
    for arm, calls, out, price, ask, cost in rows:
        band = next(b for b, lo, hi in BANDS if lo <= ask < hi)
        for key in ((arm, "all"), (arm, band)):
            a = g[key]
            a["n"] += 1
            a["calls"] += calls or 0
            if out == "receipt" and price:
                a["rc"] += 1
                a["price"].append(price)
                a["ratio"].append(price / ask)
                if cost:
                    a["margin"].append((price - cost) / cost)
    return g


def text(t0, t1):
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=30)
    try:
        c, h = claim_by_grade(db, t0, t1), haggle_by_band(db, t0, t1)
    finally:
        db.close()
    out = [f"11:00 보고 추가분 ({datetime.fromtimestamp(t0):%m-%d %H:%M} ~ {datetime.fromtimestamp(t1):%m-%d %H:%M})",
           "[등급 표기 실험] 비식품, arm x 등급: 협상 / 성사율 / 평균 판매가 / 판매가÷첫 호가 / 별점(n)"]
    for arm in ("claim", "none"):
        for gr in ("A", "B", "C"):
            a = c.get((arm, gr))
            if not a:
                continue
            out.append(f"  {arm:5} {gr}: {a['n']} / {a['rc'] / a['n']:.0%} / {_avg(a['price']):,} / "
                       f"{_avg(a['ratio'], 3)} / {_avg(a['stars'], 2)} ({len(a['stars'])})")
    out.append("[코드 협상 실험] arm x 첫 호가 금액대: 협상 / 성사율 / 마진 / 평균 판매가 / 판매가÷첫 호가 / LLM 호출(협상당)")
    for band in ["all"] + [b for b, _, _ in BANDS]:
        for arm in ("code", "harness"):
            a = h.get((arm, band))
            if not a:
                continue
            out.append(f"  {band:9} {arm:7}: {a['n']} / {a['rc'] / a['n']:.1%} / "
                       f"{(_avg(a['margin'], 3) or 0):.1%} / {(_avg(a['price']) or 0):,} / {_avg(a['ratio'], 3)} / "
                       f"{a['calls']} ({a['calls'] / a['n']:.2f})")
    return "\n".join(out)


def main():
    if "--dry" in sys.argv:
        print(text(T0, min(T1, time.time())))
        return
    while time.time() < AT:
        time.sleep(min(60, max(1, AT - time.time())))
    import alerts
    alerts.send("daily", "report_1100_extra:20261108", text(T0, T1))


if __name__ == "__main__":
    main()
