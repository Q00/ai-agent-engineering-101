"""Post-settlement evidence-based review hook for Kit v4.

Called as: python review_r3.py --deal <path-to-.market/deal.json>
Only runs after a settlement. No API requests, tokens, or unverified claims.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def summarize(d):
    order = d.get("order") or {}
    receipt = d.get("receipt") or {}
    settled = d.get("settled") or {}
    if not settled:
        return {"stars": 2, "review": "No verified settlement receipt available."}
    price = receipt.get("price")
    budget = order.get("budget")
    if isinstance(price, (int, float)) and isinstance(budget, (int, float)) and budget > 0:
        if price <= 0.9 * budget:
            return {"stars": 5, "review": "Payment settled; agreed price within budget with a comfortable margin."}
        if price <= budget:
            return {"stars": 4, "review": "Payment settled; agreed price within budget."}
    return {"stars": 3, "review": "Payment settled; insufficient price evidence for a stronger assessment."}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", required=True)
    args = ap.parse_args()
    d = json.loads(Path(args.deal).read_text(encoding="utf-8"))
    print(json.dumps(summarize(d), ensure_ascii=False), flush=True)

if __name__ == "__main__":
    main()
