"""Turn an exported dashboard snapshot into a reproducible opening-price policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def adapt(snapshot: dict) -> dict:
    shop = snapshot["shop"]
    deals = int(shop.get("deals") or 0)
    reviews = int(shop.get("reviews") or 0)
    stars = shop.get("stars")
    share = float(shop.get("share") or 0)
    average_price = round(float(shop.get("revenue") or 0) / deals, 2) if deals else None
    if reviews >= 2 and stars is not None and float(stars) < 3:
        multiplier, reason = 1.08, "low buyer ratings; reduce the opening ask toward reserve"
    elif deals >= 2 and share < 0.10:
        multiplier, reason = 1.12, "low market share after several deals; reduce the opening ask"
    elif reviews >= 2 and stars is not None and float(stars) >= 4 and share >= 0.20:
        multiplier, reason = 1.25, "high share and positive reviews; leave more room to negotiate"
    else:
        multiplier, reason = 1.20, "limited or mixed feedback; use a moderate opening ask"
    return {
        "source": snapshot.get("source", "dashboard snapshot"),
        "captured_at": snapshot.get("captured_at"), "round": snapshot.get("round"),
        "feedback": {"deals": deals, "share": share, "reviews": reviews,
                     "average_stars": stars, "average_price": average_price},
        "opening_max_reserve_multiplier": multiplier, "reason": reason,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--output", default="pricing-policy.json")
    args = parser.parse_args()
    source = Path(args.snapshot)
    snapshot = json.loads(source.read_text(encoding="utf-8"))
    snapshot["source"] = str(source)
    policy = adapt(snapshot)
    Path(args.output).write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(policy, ensure_ascii=False))


if __name__ == "__main__":
    main()
