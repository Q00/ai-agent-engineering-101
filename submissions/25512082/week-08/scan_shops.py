"""Challenge 2: score a shops.json the kit wrote with the buyer's own choose_shop rules.

Usage: python scan_shops.py .market/shops.json [logs/market-kit.log]

Prints every shop the buyer would penalise for review integrity warnings or remembered
receipt mismatches, its score next to the shop it picks, and, given the kit log, how
often the buyer actually chose each shop. No model is called.
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

from market_buyer import _review_summary, choose_shop, load_memory, load_shops


def main() -> None:
    shops = load_shops(Path(sys.argv[1]))
    memory = load_memory(Path(".market/runtime-state/buyer-memory.json"))
    pick = choose_shop(shops, memory)
    print(f"{len(shops)} shops; buyer picks {pick.shop.name}: {pick.reason}")
    for shop in shops:
        average, suspicious, summary = _review_summary(shop.reviews)
        history = memory["shops"].get(shop.name, {})
        if not suspicious and not history.get("receipt_mismatches"):
            continue
        score = choose_shop((shop,), memory).score
        print(f"\nflagged {shop.name}: {summary}; memory {history or 'none'}; score {score:.2f}")
        for review in shop.reviews:
            print(f"  {review.get('stars')}* round {review.get('round')}: {str(review.get('review'))[:140]}")
    if len(sys.argv) > 2:
        text = Path(sys.argv[2]).read_text(encoding="utf-8", errors="ignore")
        chosen = Counter(re.findall(r"\[buyer\] \[choose\] (\S+) ", text))
        print(f"\nkit log: market_buyer choices {dict(chosen)}")


if __name__ == "__main__":
    main()
