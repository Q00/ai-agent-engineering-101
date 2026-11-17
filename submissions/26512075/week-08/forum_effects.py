"""Local observational post/sales comparisons, never a causal attribution claim.

Only completed ACP sales are counted. Missing cost, quote, refund or stock
coverage produces an unavailable comparison instead of fabricated net margin.
"""
import argparse
from contextlib import contextmanager
import json
import sqlite3
from pathlib import Path
import time
import forum_delivery
import shop_ads


@contextmanager
def database(state):
    state = Path(state)
    state.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(state / "forum-effects.sqlite3", timeout=2)
    db.executescript("""
      CREATE TABLE IF NOT EXISTS quotes (sku TEXT PRIMARY KEY, ts REAL, price REAL);
      CREATE TABLE IF NOT EXISTS stock (ts REAL, sku TEXT, qty INTEGER, price REAL, PRIMARY KEY(ts,sku));
      CREATE TABLE IF NOT EXISTS sales (ledger INTEGER PRIMARY KEY, ts REAL, sku TEXT, unit TEXT,
        price REAL, cost REAL, storage REAL, refund REAL, shop TEXT);
    """)
    try:
        yield db
        db.commit()
    finally:
        db.close()


def quote(state, sku, price, now=None):
    with database(state) as db:
        db.execute("INSERT OR REPLACE INTO quotes VALUES (?,?,?)", (sku, time.time() if now is None else now, price))


def snapshot(state, inventory, now=None):
    now = time.time() if now is None else now
    # No seller policy/hold observation means unknown availability, not zero demand.
    availability = Path(state) / "seller-availability.json"
    if not availability.exists():
        return
    policy = json.loads(availability.read_text())
    holds = policy["holds"]
    minimum = policy["min_food_hours"] * 3600
    counts = {}
    for u in inventory.get("units", []):
        if (u.get("grade") in policy["allowed_grades"] and holds.get(u.get("unit"), 0) <= now
                and (u.get("expires_at") is None or u["expires_at"] - now >= minimum)):
            counts[u["sku"]] = counts.get(u["sku"], 0) + 1
    with database(state) as db:
        known = {r[0] for r in db.execute("SELECT DISTINCT sku FROM stock")} | set(counts)
        quotes = {s: price for s, ts, price in db.execute("SELECT * FROM quotes") if 0 <= now-ts <= 300}
        db.executemany("INSERT OR REPLACE INTO stock VALUES (?,?,?,?)",
                       [(now, sku, counts.get(sku, 0), quotes.get(sku)) for sku in known])


def settled(state, before, completed, shop, now=None):
    if completed.get("status") != "completed":
        return
    now = time.time() if now is None else now
    mr = completed["order"]["mandate_receipt"]
    unit = mr.get("unit") or completed.get("unit")
    old = next((u for u in before.get("units", []) if u.get("unit") == unit), None)
    if not old:
        raise ValueError("cannot associate settlement with acquisition cost")
    cost = old.get("cost")
    storage = (cost * .01 * max(0, now-old["supplied_at"]) / 86400
               if old.get("kind") == "goods" and cost is not None and old.get("supplied_at") is not None
               else 0 if old.get("kind") == "food" else None)
    # No seller warranty => no warranty refund. Positive/unknown coverage stays unknown.
    warranty = completed.get("fulfillment", {}).get("warranty_hours", 0)
    refund = 0 if warranty == 0 else None
    with database(state) as db:
        db.execute("INSERT OR IGNORE INTO sales VALUES (?,?,?,?,?,?,?,?,?)",
                   (mr["ledger"], now, old["sku"], unit, completed["totals"][0]["amount"], cost, storage, refund, shop))

    shop_ads.record_settlement(state,completed,unit,old['sku'],completed['totals'][0]['amount'],
                               cost,storage,refund,now)


def reconcile_refund(state, ledger, total_refund):
    if type(total_refund) not in (int, float) or not 0 <= total_refund < float("inf"):
        raise ValueError("invalid refund total")
    with database(state) as db:
        db.execute("UPDATE sales SET refund=? WHERE ledger=?", (total_refund, ledger))


def window(db, sku, start, duration=1800):
    end = start + duration
    rows = db.execute("SELECT ts,qty,price FROM stock WHERE sku=? AND ts>=? AND ts<=? ORDER BY ts",
                      (sku, start-30, end)).fetchall()
    available = covered = qty_seconds = price_seconds = priced = 0
    for (ts, qty, price), (next_ts, _, _) in zip(rows, rows[1:]):
        if next_ts-ts > 30:
            continue
        seconds = max(0, min(end, next_ts)-max(start, ts))
        covered += seconds
        if qty > 0:
            available += seconds
            qty_seconds += seconds * qty
            if price is not None:
                price_seconds += seconds * price
                priced += seconds
    sales = db.execute("SELECT price,cost,storage,refund FROM sales WHERE sku=? AND ts>=? AND ts<?", (sku,start,end)).fetchall()
    complete = all(all(v is not None for v in row) for row in sales)
    return {"start": start, "end": end, "settlements": len(sales),
            "net_margin": sum(p-c-s-r for p,c,s,r in sales) if complete else None,
            "available_seconds": available, "observed_seconds": covered,
            "mean_stock": qty_seconds/available if available else None,
            "mean_quote": price_seconds/priced if priced >= .9*available and priced else None,
            "complete": covered >= duration*.95 and complete}


def report(state, now=None):
    now = time.time() if now is None else now
    path = Path(state) / "forum-delivery.jsonl"
    posts = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
    posts = forum_delivery.resolved(state, posts)
    posts = [p for p in posts if p.get("state") == "sent" and p.get("post_id") is not None and p.get("sent_at") is not None]
    results = []
    with database(state) as db:
        for post in posts:
            start, sku = post["sent_at"], post["sku"]
            if start+1800 > now:
                continue
            after = window(db, sku, start)
            control = None
            # Same time of day on prior days. Missing similar stock/price means no comparator.
            for days in range(1,8):
                candidate = window(db, sku, start-days*86400)
                overlapping = any(p.get("sku") == sku and candidate["start"]-1800 <= p["sent_at"] < candidate["end"] for p in posts)
                if overlapping or not after["complete"] or not candidate["complete"]:
                    continue
                if any(after[k] is None or candidate[k] is None or abs(after[k]-candidate[k]) > .1*max(after[k],1)
                       for k in ("mean_stock", "mean_quote", "available_seconds")):
                    continue
                control = candidate
                break
            results.append({"post_id": post["post_id"], "sku": sku, "role": post["as"], "sent_at": start,
                            "after": after, "control": control,
                            "status": "observational_comparison" if control else "insufficient_comparable_exposure",
                            "settlement_difference": after["settlements"]-control["settlements"] if control else None,
                            "net_margin_difference": after["net_margin"]-control["net_margin"] if control else None})
    return {"generated_at": now, "advertisement_conversions": shop_ads.effects(state), "comparisons": results,
            "limitations": ["Association is not causation.", "Only this runtime's own seller completions are recorded.",
                            "Availability is sampled; quoted prices and stock must be comparable.",
                            "Storage is prorated at the official 1% per day; positive-warranty refunds require reconciliation."]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path(".market"))
    args = parser.parse_args()
    print(json.dumps(report(args.state), ensure_ascii=False, indent=2))
