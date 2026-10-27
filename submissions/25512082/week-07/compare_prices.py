"""Challenge 1: share and average price before and after the policy asks, from the dashboard and logs.

Usage: python compare_prices.py

Before: the first run, 14:46:02 to 17:40:18 KST, model-chosen opening asks; its totals are the
dashboard row measured after it stopped (101 deals). After: the run started 18:10:50 KST with
pricing-policy.json; dashboard deltas over those totals, plus logs/challenge-1/seller-trades.jsonl.
"""
from __future__ import annotations

import json
import statistics
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
KST = ZoneInfo("Asia/Seoul")
BEFORE = {"start": "2026-10-27T14:46:02+09:00", "end": "2026-10-27T17:40:18+09:00",
          "deals": 101, "visits": 179, "revenue": 22492, "profit": 2712}
AFTER_START = "2026-10-27T18:10:50+09:00"


def board():
    with urllib.request.urlopen("https://market.ourolabs.ai/api/board?round=2", timeout=20) as r:
        return json.load(r)


def describe(label: str, hours: float, deals: int, visits: int, revenue: int, profit: int,
             market_deals: int | None) -> None:
    print(f"{label}: {hours:.2f} h, deals {deals}, visits {visits}, conversion {deals / visits:.1%}, "
          f"deals/h {deals / hours:.1f}, average price {revenue / deals:.1f}, "
          f"average margin {profit / deals:.1f}, profit/h {profit / hours:.0f}"
          + (f", share of market deals {deals / market_deals:.3%}" if market_deals else ""))


def main() -> None:
    now = datetime.now(KST)
    b = board()
    row = next(x for x in b["shops"] if x["name"] == "sohyunkim11")
    print(f"measured {now.isoformat(timespec='seconds')}; dashboard row: "
          + json.dumps({k: v for k, v in row.items() if k not in ("card", "owner")}))
    print(f"dashboard share field (as the market reports it): {row.get('share')}")

    snapshots = [json.loads(line) for line in (HERE / "logs/operations/leaderboard.jsonl").open()]
    totals = {s["measured_at_kst"]: s["totals"]["deals"] for s in snapshots if "totals" in s}
    first = min(t for t in totals if t >= "2026-10-27T14:47")
    before_market = totals["2026-10-27T17:41:54+09:00"] - totals[first]
    after_market = b["totals"]["deals"] - totals["2026-10-27T18:08:31+09:00"]

    start, end = (datetime.fromisoformat(BEFORE[k]) for k in ("start", "end"))
    describe("before (model asks)", (end - start).total_seconds() / 3600, BEFORE["deals"], BEFORE["visits"],
             BEFORE["revenue"], BEFORE["profit"], before_market)
    after_hours = (now - datetime.fromisoformat(AFTER_START)).total_seconds() / 3600
    deltas = {k: row[k] - BEFORE[k] for k in ("deals", "visits", "revenue", "profit")}
    if deltas["deals"] and deltas["visits"]:
        describe("after (policy asks)", after_hours, deltas["deals"], deltas["visits"], deltas["revenue"],
                 deltas["profit"], after_market)

    trades = [json.loads(line) for line in (HERE / "logs/challenge-1/seller-trades.jsonl").open()]
    by_item = defaultdict(list)
    for t in trades:
        by_item[t["item"]].append(t)
    print(f"\nseller-trades.jsonl (after only; the first run did not log trades): {len(trades)} finished negotiations")
    for item, rows in sorted(by_item.items()):
        done = [t["price"] for t in rows if t["outcome"] == "completed"]
        asks = sorted({t["opening_ask"] for t in rows})
        print(f"  {item}: opening asks {asks}, completed {len(done)}, rejected {len(rows) - len(done)}"
              + (f", average price {statistics.mean(done):.1f}, at the ask {sum(p == asks[-1] for p in done)}"
                 if done else ""))


if __name__ == "__main__":
    main()
