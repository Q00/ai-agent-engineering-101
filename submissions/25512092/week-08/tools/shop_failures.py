"""Why my shop's negotiations do not end in a paid deal: the buyer cancelled, or took a receipt and never paid.
Splits both by grade at the time, shelf life left (food), certificate in the first reply, and price
(first ask / market average then). For receipts that were not paid, the buyer's result entry on the ledger
(matched by the receipt's task) adds its stars and review text. Appends a section to
reports/throughput_YYYYMMDD.md.

    python tools/shop_failures.py
"""
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime

import httpx

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from market_db import sku_avg  # noqa: E402

FOOD_CUTS = (0.8, 0.45)          # market_kit.FOOD_GRADE_CUTS: share of shelf life left for food grade A, B
CERT_FIX = datetime(2026, 11, 6, 0, 50).timestamp()   # c6c5e96: the first reply carries the certificate
SETTLE_GRACE_S = 900
MARKET = "https://market.ourolabs.ai"


def results_since(ts0):
    """Ledger result entries after ts0 (read backwards page by page until older than ts0)."""
    out, c = [], httpx.Client(timeout=30)
    after = -500
    first = c.get(f"{MARKET}/api/ledger", params={"after": after}).json()
    top = first[-1]["seq"]
    seq = top - 500
    page = first
    while True:
        out += [e for e in page if e["kind"] == "result"]
        if not page or page[0]["ts"] < ts0:
            break
        seq -= 500
        page = c.get(f"{MARKET}/api/ledger", params={"after": seq}).json()[:500]
    return {(e["data"].get("receipt") or {}).get("task"): e["data"] for e in out if e["data"].get("receipt")}


def bucket_price(r):
    if r is None:
        return "no market average"
    return "<= 100% of avg" if r <= 1.0 else "100-105%" if r <= 1.05 else "105-110%" if r <= 1.10 else "> 110%"


def bucket_shelf(h):
    if h is None:
        return "goods"
    return "< 24 h" if h < 24 else "24-72 h" if h < 72 else "72-168 h" if h < 168 else ">= 168 h"


def main():
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=30)
    catalog = json.load(open(os.path.join(HERE, ".market", "catalog.json"), encoding="utf-8"))
    inv = json.load(open(os.path.join(HERE, ".market", "inventory.json"), encoding="utf-8"))
    held = {u["unit"]: u for u in inv.get("units", [])}
    now = time.time()
    rows = db.execute(
        "SELECT d.task, d.ts, d.sku, d.unit, d.first_ask, d.outcome, m.grade, m.supplied_at, "
        "EXISTS(SELECT 1 FROM settles s WHERE s.unit=d.unit AND s.ts>=d.ts-60) FROM shop_deals d "
        "LEFT JOIN my_stock m ON m.unit=d.unit WHERE d.ts < ?", (now - SETTLE_GRACE_S,)).fetchall()
    groups = {"cancelled by the buyer": [], "receipt, not paid": []}
    for task, ts, sku, unit, ask, outcome, grade, supplied, settled in rows:
        if outcome == "cancelled":
            groups["cancelled by the buyer"].append((task, ts, sku, unit, ask, grade, supplied))
        elif outcome == "receipt" and not settled:
            groups["receipt, not paid"].append((task, ts, sku, unit, ask, grade, supplied))
    t0 = min((r[1] for g in groups.values() for r in g), default=now)
    results = results_since(t0 - 60) if groups["receipt, not paid"] else {}

    lines = ["", f"## Shop: cancelled and unpaid negotiations, broken down ({datetime.now():%m-%d %H:%M})", "",
             f"shop_deals rows older than {SETTLE_GRACE_S // 60} min. Grade: goods as supplied; food from the share "
             "of shelf life left at the negotiation (A >= 80%, B >= 45%). Certificate: the first reply carried "
             f"none before {datetime.fromtimestamp(CERT_FIX):%m-%d %H:%M} (c6c5e96); the checkout always did.", ""]
    for name, g in groups.items():
        dims = {"grade": Counter(), "shelf life left": Counter(), "certificate in first reply": Counter(),
                "first ask vs market average": Counter()}
        reviews = Counter()
        for task, ts, sku, unit, ask, grade, supplied in g:
            c = catalog.get(sku) or {}
            hours = None
            if c.get("kind") == "food":
                exp = (held.get(unit) or {}).get("expires_at") or (
                    (supplied or 0) + (c.get("shelf_hours") or 0) * 3600 if supplied and c.get("shelf_hours") else None)
                if exp:
                    hours = (exp - ts) / 3600
                    share = hours / c["shelf_hours"]
                    grade = "A" if share >= FOOD_CUTS[0] else "B" if share >= FOOD_CUTS[1] else "C"
            avg, _ = sku_avg(db, sku, ts)
            dims["grade"][grade or "?"] += 1
            dims["shelf life left"][bucket_shelf(hours)] += 1
            dims["certificate in first reply"]["yes" if ts >= CERT_FIX else "no"] += 1
            dims["first ask vs market average"][bucket_price(ask / avg if avg and ask else None)] += 1
            r = results.get(task)
            if name.startswith("receipt"):
                reviews[f"{r.get('stars')} stars: {str(r.get('review'))[:70]}" if r else "no result entry found"] += 1
        lines += [f"### {name}: {len(g)}", "", "| dimension | split |", "|---|---|"]
        lines += [f"| {d} | " + ", ".join(f"{k} {v}" for k, v in cnt.most_common()) + " |" for d, cnt in dims.items()]
        if reviews:
            lines += ["", "Buyer's result entry for these receipts:", ""]
            lines += [f"- {k}: {v}" for k, v in reviews.most_common(8)]
        lines.append("")
    path = os.path.join(HERE, "reports", f"throughput_{datetime.now():%Y%m%d}.md")
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print(f"-> appended to {path}")


if __name__ == "__main__":
    main()
