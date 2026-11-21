"""Builds a fake Round 3 world in a scratch folder so restock, the seller and the manager run before the market opens.

    python tests/fake_round3.py OUT_DIR [--hours 6] [--catalog catalog.json] [--demand demand.json]

OUT_DIR/data/market.db   the collector's schema with Round 3 settle and broken entries: a few hidden hot categories
                         (orders pick a category), our shop taking some of the deals, goods breaking by grade
OUT_DIR/.market/         inventory.json (balance 5,000,000, a few units), catalog.json, demand.json, forum.json

Field names follow agent.md (settle: item is the SKU; broken: buyer, item). The market's real field set is checked
on 11-03; model.py reads every field defensively.
"""
import argparse
import json
import random
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from collector import SCHEMA  # noqa: E402

OUR_URL = "https://our-shop.trycloudflare.com"
BREAK = {"A": 0.05, "B": 0.10, "C": 0.25}       # the hidden truth the estimator should find
US = "inextro"                                  # our buyer: pays 5% under the market at the same shop


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--hours", type=float, default=6)
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--demand", required=True)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    random.seed(a.seed)
    out = Path(a.out)
    (out / "data").mkdir(parents=True, exist_ok=True)
    (out / ".market").mkdir(parents=True, exist_ok=True)
    catalog = json.loads(Path(a.catalog).read_text(encoding="utf-8"))
    demand = json.loads(Path(a.demand).read_text(encoding="utf-8"))
    by_cat = {}
    for x in catalog:
        by_cat.setdefault(x["category"], []).append(x)

    db_path = out / "data" / "market.db"
    db_path.unlink(missing_ok=True)
    db = sqlite3.connect(db_path)
    db.executescript(SCHEMA)
    db.executemany("INSERT INTO catalog VALUES (?, ?, ?, ?, ?, ?, ?)",
                   [(x["sku"], x["name"], x["category"], x["list_price"], x["kind"], x.get("shelf_hours"), 0)
                    for x in catalog])
    db.execute("INSERT INTO shops VALUES (?, ?, ?, ?, ?)", (OUR_URL, "inextro", "", 0, 0))

    cats = sorted(by_cat)
    hot = random.sample(cats, 8)                    # hidden: most orders come from these
    weights = {c: (20.0 if c in hot else 1.0) for c in cats}
    total_rate = 600.0                              # settled deals per hour, whole market
    now = time.time()
    start = now - a.hours * 3600
    seq, t = 1, start
    db.execute("INSERT INTO ledger VALUES (?, ?, ?, ?, ?, ?)", (seq, start, 3, "round", json.dumps({"round": 3}), ""))
    sold = []
    ratio = {s: random.uniform(0.65, 0.95) for s in [OUR_URL] + [f"https://shop-{i}.example" for i in range(1, 41)]}
    while True:
        t += random.expovariate(total_rate / 3600)
        if t >= now:
            break
        c = random.choices(cats, [weights[x] for x in cats])[0]
        x = random.choice(by_cat[c])
        grade = random.choices("ABC", [70, 20, 10] if x["kind"] == "goods" else [25, 50, 25])[0]
        shop = OUR_URL if random.random() < 0.05 else f"https://shop-{random.randint(1, 40)}.example"
        buyer = US if random.random() < 0.03 and shop != OUR_URL else f"buyer{random.randint(1, 60)}"
        r = random.lognormvariate(0, 0.08) * ratio[shop] * (0.95 if buyer == US else 1.0)
        price = round(x["list_price"] * r)
        seq += 1
        d = {"shop": shop, "buyer": buyer, "item": x["sku"], "amount": price, "grade": grade, "claim": grade,
             "claim_honest": True}
        db.execute("INSERT INTO ledger VALUES (?, ?, ?, ?, ?, ?)", (seq, t, 3, "settle", json.dumps(d), ""))
        if x["kind"] == "goods" and random.random() < BREAK[grade]:
            sold.append((t + random.uniform(0, 6 * 3600), buyer, x["sku"]))
    (out / "truth.json").write_text(json.dumps(ratio, indent=1), encoding="utf-8")
    for bt, buyer, sku in sorted(sold):
        if bt < now:
            seq += 1
            db.execute("INSERT INTO ledger VALUES (?, ?, ?, ?, ?, ?)",
                       (seq, bt, 3, "broken", json.dumps({"buyer": buyer, "item": sku}), ""))
    db.commit()

    md = out / ".market"
    units = []
    for i, x in enumerate(random.sample([x for x in catalog if x["list_price"] < 100000], 6)):
        g = random.choice("ABC")
        units.append({"unit": f"u{i}", "sku": x["sku"], "name": x["name"], "category": x["category"],
                      "list_price": x["list_price"], "kind": x["kind"], "cost": round(x["list_price"] * 0.5),
                      "grade": g, "supplied_at": now - 3600,
                      "expires_at": now + 3600 * (x.get("shelf_hours") or 0) * 0.5 if x["kind"] == "food" else None,
                      "cert": True})
    (md / "inventory.json").write_text(json.dumps({
        "round": 3, "balance": 5_000_000, "start": 5_000_000, "injected": 0, "bankruptcies_left": 2,
        "bankrupt_line": 500_000, "storage_per_day": 0.01, "units": units}, ensure_ascii=False), encoding="utf-8")
    (md / "catalog.json").write_text(json.dumps({x["sku"]: x for x in catalog}, ensure_ascii=False), encoding="utf-8")
    (md / "demand.json").write_text(json.dumps(demand, ensure_ascii=False), encoding="utf-8")
    (md / "forum.json").write_text("[]", encoding="utf-8")
    print(f"{seq} ledger entries over {a.hours} h, {len([s for s in sold if s[0] < now])} broken; hot categories:")
    for c in hot:
        print("  ", c)


if __name__ == "__main__":
    main()
