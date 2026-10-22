"""Challenge 1 — set the shop's asking prices from its own trades and reviews.

    python reprice.py                 # reads logs/market-kit.log + the board, writes pricing.json

The reserves never change. What changes is how high the shop opens.

The evidence, from the first half hour in Round 2: the harness opened at about
1.53x the reserve, deals closed at a mean of 1.14x, and no buyer ever paid more
than 1.39x. Reviews also show buyers' budgets ("Paid 270.0 of 360.") -- so an
opening ask above anything anyone has ever paid buys nothing but rounds, and
the shop closed only 13 of 28 negotiations. The rule below caps each item's
opening ask at the highest multiple of the reserve that a buyer has actually
paid for it, with a small margin, and never below a floor that leaves room to
come down.
"""
import json
import re
import urllib.request
from collections import defaultdict
from pathlib import Path

MARKET = "https://market.ourolabs.ai"
NAME = "YAHO1212"
MARGIN = 1.05          # open a little above the best price ever paid
FLOOR = 1.20           # but always leave room to concede
DEFAULT = 1.35         # an item never sold yet


def deals_from_log(path="logs/market-kit.log"):
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    out = defaultdict(list)
    for item, price, reserve in re.findall(r"DEAL (.+?) at (\d+) \(reserve (\d+)\)", text):
        out[item.strip()].append(int(price) / int(reserve))
    return out


def reviews_from_board():
    try:
        with urllib.request.urlopen(f"{MARKET}/api/board?round=2", timeout=30) as r:
            d = json.load(r)
    except Exception as e:                          # noqa: BLE001
        print(f"board unavailable ({type(e).__name__}); pricing from the log only")
        return []
    return [r for r in d.get("reviews", []) if r.get("shop") == NAME]


def main():
    deals = deals_from_log()
    reviews = reviews_from_board()

    # "Paid 270.0 of 360." -> how much budget the buyer had left on the table
    headroom = []
    for r in reviews:
        m = re.search(r"Paid ([\d.]+) of ([\d.]+)", r.get("review", ""))
        if m:
            paid, budget = float(m.group(1)), float(m.group(2))
            headroom.append(budget / paid)

    pricing = {}
    for item, ratios in deals.items():
        best = max(ratios)
        pricing[item] = round(max(FLOOR, best * MARGIN), 2)
    pricing["_default"] = DEFAULT

    report = {
        "rule": f"open at max(paid/reserve) x {MARGIN}, floor {FLOOR}, default {DEFAULT}",
        "deals_seen": {k: {"n": len(v), "mean": round(sum(v) / len(v), 2),
                           "max": round(max(v), 2)} for k, v in deals.items()},
        "reviews_seen": len(reviews),
        "buyer_budget_over_paid": (round(sum(headroom) / len(headroom), 2)
                                   if headroom else None),
        "pricing": pricing,
    }
    Path("pricing.json").write_text(json.dumps(pricing, indent=1) + "\n", encoding="utf-8")
    out = Path("logs/challenge-1")
    out.mkdir(parents=True, exist_ok=True)
    (out / "reprice.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n",
                                      encoding="utf-8")
    print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
