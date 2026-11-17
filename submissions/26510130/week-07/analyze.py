"""Analyse the whole Round 2 run, for REPORT.md parts 3 and 5.

    python analyze.py          # writes logs/analysis/summary.json and prints it

Sources, all already in this directory or on the market:
  logs/market-kit.log         every order, deal and ledger line the kit wrote
  logs/challenge-2/picks.jsonl what the buyer was offered and what it chose
  market_memory.json          what the buyer remembers about each shop
  /api/board?round=2          the market's own counts
"""
import collections
import json
import re
import statistics
import urllib.request
from pathlib import Path

NAME = "YAHO1212"
MARKET = "https://market.ourolabs.ai"
TEST_SHOPS = {"Kit Shop", "Kit Shop North", "Kit Shop South"}   # market-test residue


def mean(xs):
    return round(statistics.mean(xs), 3) if xs else None


def kit_log():
    text = Path("logs/market-kit.log").read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()

    ledger = re.findall(r"ledger #(\d+): (.+?), stars (\d), verified (\w+), review (.+)", text)
    deals = [l for l in ledger if not l[1].startswith("no deal")]
    paid = []
    for _, what, *_ in deals:
        m = re.match(r"(\w+) at ([\d.]+)", what)
        if m:
            paid.append((m.group(1), float(m.group(2))))

    orders = re.findall(r"\[order o-\w+\] (\w+), budget (\d+), round (\d)", text)
    budgets = collections.defaultdict(list)
    for item, b, _ in orders:
        budgets[item].append(int(b))

    shop_deals = [(it, int(p), int(r)) for it, p, r in
                  re.findall(r"\[seller\] DEAL (\S+) at (\d+) \(reserve (\d+)\)", text)]
    joins = [l[:8] for l in lines if f"shop registered as {NAME}" in l]

    return {
        "lines": len(lines),
        "joins": len(joins),
        "orders": len(orders),
        "orders_by_item": {k: len(v) for k, v in budgets.items()},
        "budget_range_by_item": {k: [min(v), max(v)] for k, v in budgets.items()},
        "ledger_lines": len(ledger),
        "buyer_deals": len(deals),
        "buyer_no_deals": len(ledger) - len(deals),
        "buyer_deals_verified": sum(1 for d in deals if d[3] == "True"),
        "buyer_mean_price_by_item": {it: mean([p for i, p in paid if i == it])
                                     for it in {i for i, _ in paid}},
        "no_result_line": text.count("printed no result line"),
        "guard_refused_accept": text.count("REFUSED harness accept"),
        "receipt_mismatch_seen": text.count("RECEIPT MISMATCH"),
        "shop_deals": len(shop_deals),
        "shop_markup_by_item": {it: mean([p / r for i, p, r in shop_deals if i == it])
                                for it in {i for i, _, _ in shop_deals}},
        "shop_sold_below_reserve": sum(1 for _, p, r in shop_deals if p < r),
    }


def picks():
    p = Path("logs/challenge-2/picks.jsonl")
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    offered_acc, picked_acc, example = collections.Counter(), collections.Counter(), {}
    for r in rows:
        for o in r["offered"]:
            if o.get("accused"):
                offered_acc[o["name"]] += 1
                example.setdefault(o["name"], o["accused"][0])
                if r["picked"] == o["name"]:
                    picked_acc[o["name"]] += 1
    shops_offered = [len(r["offered"]) for r in rows]
    return {
        "orders_recorded": len(rows),
        "first": rows[0]["ts"] if rows else None,
        "last": rows[-1]["ts"] if rows else None,
        "shops_per_list": [min(shops_offered), max(shops_offered)] if rows else None,
        "picked_top": collections.Counter(r["picked"] for r in rows).most_common(10),
        "distinct_shops_picked": len({r["picked"] for r in rows}),
        "accused": {n: {"offered": offered_acc[n], "picked": picked_acc[n],
                        "example": example[n]} for n in offered_acc},
    }


def memory():
    m = json.loads(Path("market_memory.json").read_text(encoding="utf-8"))["shops"]
    real = {k: v for k, v in m.items() if k not in TEST_SHOPS}
    rows = sorted(real.items(), key=lambda kv: -(kv[1].get("deals", 0) + kv[1].get("no_deals", 0)))
    return {
        "shops_remembered": len(real),
        "blacklisted": [k for k, v in real.items() if v.get("cheated", 0) > 0],
        "top": [{"name": k, "deals": v.get("deals", 0), "no_deals": v.get("no_deals", 0),
                 "avg_price": mean(v.get("prices", []))} for k, v in rows[:8]],
        "test_residue": [k for k in m if k in TEST_SHOPS],
    }


def board():
    with urllib.request.urlopen(f"{MARKET}/api/board?round=2", timeout=30) as r:
        d = json.load(r)
    shops = [s for s in d["shops"] if s.get("deals")]
    shop = next(s for s in d["shops"] if s["name"] == NAME)
    buyer = next(b for b in d["buyers"] if b["name"] == NAME)
    by_deals = sorted(shops, key=lambda s: -s["deals"])
    by_stars = sorted([s for s in shops if s.get("stars") and s["deals"] >= 20],
                      key=lambda s: -s["stars"])
    rank = lambda lst: next((i + 1 for i, s in enumerate(lst) if s["name"] == NAME), None)
    reviews = [r for r in d["reviews"] if r.get("shop") == NAME]
    return {
        "shop": {k: shop.get(k) for k in ("deals", "visits", "share", "stars", "revenue",
                                          "profit", "alive")},
        "buyer": {k: buyer.get(k) for k in ("orders", "deals", "paid", "saved", "held",
                                            "hopeless", "alive")},
        "shops_with_deals": len(shops),
        "rank_by_deals": rank(by_deals),
        "rank_by_stars_min20": [rank(by_stars), len(by_stars)],
        "top_by_deals": [(s["name"], s["deals"], s.get("stars"), s.get("house"))
                         for s in by_deals[:8]],
        "dishonest_house": [(s["name"], s.get("deals"), s.get("stars"))
                            for s in d["shops"] if s.get("house")],
        "recent_reviews_of_me": [(r["stars"], r["review"][:90]) for r in reviews[:8]],
    }


def main():
    out = {"kit": kit_log(), "picks": picks(), "memory": memory(), "board": board()}
    dst = Path("logs/analysis")
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "summary.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n",
                                      encoding="utf-8")
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
