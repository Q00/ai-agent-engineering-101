"""Our buyer, the kit's market_buyer hook (Round 3):

    "market_buyer": {"cmd": "py market_buyer.py --shops {shops} --item {item} --budget {budget} --order {order}"}

The kit writes the shop list and the order to .market/, runs this, and reads the last JSON line:
{"shop": <url>, "receipt": {...} or null, "stars": null, "review": null, "accept": {...}}. Stars and the
review come later from review.py, after the kit has paid. Every decision is in agents.buyer_core.
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from agents.buyer_core import BuyerCore, load_inputs  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item")
    ap.add_argument("--budget", type=float)
    ap.add_argument("--order")
    a = ap.parse_args()
    order, shops = load_inputs(a.shops, a.order)
    order.setdefault("item", a.item)
    if order.get("budget") is None and a.budget is not None:
        order["budget"] = int(a.budget) if a.budget.is_integer() else a.budget
    core = BuyerCore(order, shops)
    try:
        out = core.run()
    finally:
        core.close()
    print(json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                      # no result line: the kit closes the order as no deal
        print(f"market_buyer crashed: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
