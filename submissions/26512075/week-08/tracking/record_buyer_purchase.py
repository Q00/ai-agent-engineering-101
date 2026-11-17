"""Record the kit's settled deal via review hook; preserve its existing review."""
import argparse
import json
import os
import sys
import time
from pathlib import Path


def record(path):
    path = Path(path)
    deal = json.loads(path.read_text())
    settled, order = deal["settled"], deal["order"]
    if order.get("round") != 3 or type(settled.get("ledger")) is not int:
        raise ValueError("requires a Round 3 market settlement")
    event = {"ledger": settled["ledger"], "orderId": order["order"],
             "shopUrl": settled["shop"], "sku": settled["sku"],
             "unit": settled.get("unit"), "name": settled["name"],
             "grade": settled.get("grade"), "warrantyHours": settled.get("warranty_hours"),
             "observedAt": time.time(), "timeSource": "post_settlement_hook",
             "source": "kit_deal_settled"}
    destination = path.parent / "buyer-purchases.jsonl"
    existing = []
    if destination.exists():
        existing = [json.loads(line) for line in destination.read_text().splitlines() if line.strip()]
    if any(row.get("ledger") == event["ledger"] for row in existing):
        return
    with destination.open("a") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deal", required=True)
    args = parser.parse_args()
    try:
        record(args.deal)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"purchase observation failed: {type(exc).__name__}", file=sys.stderr)
    # No stars => kit keeps the buyer's original stars/review.
    print("{}")
