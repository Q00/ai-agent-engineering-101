"""Challenge 2, market evidence: how the buyer treated a dishonest shop it met in the market.

    py challenge2_evidence.py maple-home > logs/challenge-2/market-evidence-<time>.txt

Reads logs/market-kit.log (read only) and logs/challenge-2/shops-snapshot.json (a copy of
.market/shops.json the kit wrote for one order) and prints:
  - the shop's reviews in the snapshot,
  - how many orders listed shops, and how many of them the buyer took to this shop,
  - every [choose] line that names this shop (haiku's reason for going elsewhere).
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "logs", "market-kit.log")
SNAPSHOT = os.path.join(HERE, "logs", "challenge-2", "shops-snapshot.json")


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "maple-home"
    shops = json.load(open(SNAPSHOT, encoding="utf-8"))
    shop = next((s for s in shops if s["name"] == name), None)
    print(f"== {name} in the shop list (snapshot of .market/shops.json)")
    if shop is None:
        print("  not in the snapshot")
    else:
        for r in shop.get("reviews") or []:
            print(f"  {r.get('stars')}* round {r.get('round')}: {r.get('review')}")
        others = [s for s in shops if s["name"] != name and s.get("reviews")]
        avg = lambda s: sum(r["stars"] for r in s["reviews"]) / len(s["reviews"])
        print(f"  average {avg(shop) if shop.get('reviews') else 0:.1f} stars; "
              f"the other {len(others)} reviewed shops average {sum(map(avg, others)) / len(others):.1f}")

    lines = open(LOG, encoding="utf-8", errors="ignore").read().splitlines()
    listed = [l for l in lines if re.search(r"\[kit\] \d+ shops in .*running market_buyer", l)]
    choose = [l for l in lines if "[buyer] [choose]" in l and "blocked" not in l]
    picked = [l for l in choose if re.search(rf": {re.escape(name)}: ", l)]
    named = [l for l in choose if name.lower() in l.lower() and l not in picked]
    blocked = [l for l in lines if "[choose] blocked" in l and name in l]
    print(f"\n== market-kit.log")
    print(f"  orders run by market_buyer: {len(listed)}")
    print(f"  orders the buyer took to {name}: {len(picked)}")
    print(f"  blocked from memory: {len(blocked)} (only after being cheated by it)")
    print(f"  reasons that name {name} while picking another shop: {len(named)}")
    for l in named:
        print("   " + l)


if __name__ == "__main__":
    main()
