"""Advisory ordering for an existing buyer; never performs a purchase."""

import argparse
import json
import time
from pathlib import Path

if __package__:
    from .analyst import REPORT_NAME, market_origin
    from .storage import read_json
else:
    from analyst import REPORT_NAME, market_origin
    from storage import read_json


def prioritize_shops(order, shops, state, market, max_age=300, now=None):
    """Return every shop, with shops offering candidate SKUs first.

    The caller must independently check the seller's current price, signed receipt,
    order constraints, and checkout policy. An old or absent report preserves order.
    """
    original = list(shops)
    now = time.time() if now is None else now
    try:
        report = read_json(Path(state) / REPORT_NAME)
        if not isinstance(report, dict) or report.get("market") != market_origin(market):
            return original
        stamps = (report.get("generated_at"), report.get("forum_snapshot_mtime"))
        if any(not isinstance(stamp, (int, float)) or not -5 <= now - stamp <= max_age for stamp in stamps):
            return original
        acceptable = order.get("acceptable")
        candidates = report.get("candidates")
        if not isinstance(acceptable, list) or not isinstance(candidates, list):
            return original
        allowed = {sku for sku in acceptable if isinstance(sku, str)}
        ranked = {row["sku"] for row in candidates if isinstance(row, dict) and isinstance(row.get("sku"), str)}
        wanted = allowed & ranked

        def priority(shop):
            if not isinstance(shop, dict) or not isinstance(shop.get("offers"), list):
                return 1
            for offer in shop["offers"]:
                if (isinstance(offer, dict) and offer.get("sku") in wanted
                        and isinstance(offer.get("qty"), (int, float)) and offer["qty"] > 0):
                    return 0
            return 1

        return sorted(original, key=priority)
    except (OSError, ValueError, TypeError):
        return original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--order", type=Path, required=True)
    parser.add_argument("--shops", type=Path, required=True)
    parser.add_argument("--state", type=Path, default=Path(".market"))
    parser.add_argument("--market", required=True)
    args = parser.parse_args()
    order = read_json(args.order)
    shops = read_json(args.shops)
    if not isinstance(order, dict) or not isinstance(shops, list):
        parser.error("order must be a JSON object and shops must be a JSON array")
    print(json.dumps(prioritize_shops(order, shops, args.state, args.market), ensure_ascii=False))


if __name__ == "__main__":
    main()
