"""Challenge 1: derive opening asks from this shop's own trades and reviews.

Usage: python update_prices.py [--board FILE] [--name sohyunkim11]

Evidence, all from this directory or the public market API (no model call):
- the shop's dashboard row (deals, visits, revenue, profit, stars) from /api/board?round=2
- the shop's own reviews from /api/shops (only listed while the shop is online)
- order budgets the market issued, from logs/market-kit.log
- earlier model-chosen opening asks, from the market-test seller logs

Rule: the opening ask is the median budget the market issued for the item, rounded to 5,
never below reserve + 5. When the shop's reviews average below 4.0 it drops 5 more; when
conversion is above 70% and stars at least 4.5 it rises 5. Reserves stay as they are.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from protocol import RESERVES

HERE = Path(__file__).resolve().parent
MARKET = "https://market.ourolabs.ai"


def fetch(path: str):
    with urllib.request.urlopen(MARKET + path, timeout=20) as response:
        return json.load(response)


def budgets_from_log(log: Path) -> dict[str, list[int]]:
    budgets: dict[str, list[int]] = {item: [] for item in RESERVES}
    for line in log.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = re.search(r"\[order \S+\] (\w+), budget (\d+)", line)
        if match and match[1] in budgets:
            budgets[match[1]].append(int(match[2]))
    return budgets


def earlier_asks(logs: Path) -> dict[str, list[int]]:
    asks: dict[str, list[int]] = {item: [] for item in RESERVES}
    for path in (logs / "market-test.log", logs / "trade-test.log"):
        for item, price in re.findall(r"(lamp|chair|bookshelf) asking price: (\d+)", path.read_text(errors="ignore")):
            asks[item].append(int(price))
    return asks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", type=Path, help="saved /api/board?round=2 JSON instead of fetching")
    parser.add_argument("--name", default="sohyunkim11")
    args = parser.parse_args()

    board = json.loads(args.board.read_text()) if args.board else fetch("/api/board?round=2")
    row = next(x for x in board["shops"] if x["name"] == args.name)
    deals, visits = row.get("deals", 0), row.get("visits", 0)
    stars = row.get("stars") or 0
    conversion = deals / visits if visits else 0.0
    print(f"dashboard: deals {deals}, visits {visits}, conversion {conversion:.2%}, "
          f"revenue {row.get('revenue')}, profit {row.get('profit')}, stars {stars}, share {row.get('share')}")
    if deals:
        print(f"dashboard: average price {row['revenue'] / deals:.1f}, average margin over reserve {row['profit'] / deals:.1f}")

    own = next((s for s in fetch("/api/shops")["shops"] if s["name"] == args.name), None)
    reviews = own["reviews"] if own else []
    review_stars = [r["stars"] for r in reviews if isinstance(r.get("stars"), int)]
    review_avg = statistics.mean(review_stars) if review_stars else stars
    print(f"own reviews listed now: {len(reviews)}" + (f", average {review_avg:.2f}" if review_stars else
                                                       " (shop offline; using dashboard stars)"))
    for r in reviews[:10]:
        print(f"  {r.get('stars')}* {str(r.get('review'))[:120]}")

    budgets = budgets_from_log(HERE / "logs" / "market-kit.log")
    before = earlier_asks(HERE / "logs")
    asks = {}
    for item, reserve in RESERVES.items():
        seen = budgets[item]
        median = statistics.median(seen)
        ask = max(reserve + 5, int(5 * round(median / 5)))
        if review_avg < 4.0:
            ask = max(reserve + 5, ask - 5)
        elif conversion > 0.70 and review_avg >= 4.5:
            ask += 5
        asks[item] = ask
        above = sum(b >= ask for b in seen) / len(seen)
        print(f"{item}: reserve {reserve}; {len(seen)} market budgets, median {median}, "
              f"min {min(seen)}, max {max(seen)}; earlier model asks {sorted(set(before[item])) or 'not logged'}; "
              f"new opening ask {ask} (within {above:.0%} of observed budgets)")

    now = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
    policy = {"version": now, "asks": asks, "reserves": RESERVES,
              "evidence": {"deals": deals, "visits": visits, "conversion": round(conversion, 4),
                           "stars": stars, "review_average": round(review_avg, 2),
                           "budgets": {k: len(v) for k, v in budgets.items()}}}
    (HERE / "pricing-policy.json").write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
    print(f"wrote pricing-policy.json version {now}: {asks}")


if __name__ == "__main__":
    main()
