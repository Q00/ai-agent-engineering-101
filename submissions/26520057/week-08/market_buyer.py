"""Bounded comparison shopping; official kit retains payment and signature authority."""
import argparse
import json
from pathlib import Path
from buyer_policy import run, log_event

ROOT = Path(__file__).resolve().parent

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--shops", type=Path, required=True)
    parser.add_argument("--order", type=Path, required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", type=int, required=True)
    args = parser.parse_args()
    try:
        order = json.loads(args.order.read_text())
        if order["budget"] != args.budget or order.get("round") != 3:
            raise ValueError("Order/budget does not match Round 3")
        result = run(order, json.loads(args.shops.read_text()),
                     json.loads((ROOT / ".market/catalog.json").read_text()))
    except Exception as exc:
        log_event(event="closed_without_purchase", reason=type(exc).__name__)
        result = {"shop": "", "receipt": None}
    print(json.dumps(result, ensure_ascii=False), flush=True)
