"""review hook (Round 3): stars and review for a settled deal, from the settled facts only.

    python review.py --deal .market/deal.json

Prints {"stars": 1-5, "review": "..."} on stdout. Keeps a copy of the deal in memory/deals/<order>.json
and a decision row in logs/decisions/review.jsonl. On any error it prints nothing, so the kit keeps
market_buyer's provisional stars. Then queues one buyer forum post for the deal (tools/forum.py).
"""
import argparse
import json
import time
from pathlib import Path

import policy
from memory.decisions import decision
from memory.store import LOGS, MEM, atomic_write_json, hook_log, tail_jsonl
from tools import forum
from tools.buyer_policy import rate_deal


def buyer_row(order_id, n=500):
    """market_buyer's decision row for this order: what was agreed (sku, unit, V)."""
    for row in reversed(tail_jsonl(LOGS / "decisions" / "buyer.jsonl", n)):
        if row.get("kind") == "order" and (row.get("ref") or {}).get("order") == order_id:
            return row
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--deal", required=True, type=Path)
    args = ap.parse_args(argv)
    try:
        pol, ver, _ = policy.load()
        deal = json.loads(args.deal.read_text(encoding="utf-8"))
        order = deal.get("order") or {}
        order_id = order.get("order")
        settled = deal.get("settled") or {}
        row = buyer_row(order_id)
        stars, review = rate_deal(deal, row, provisional=False)
        if order_id:
            atomic_write_json(MEM / "deals" / f"{order_id}.json", deal)
        receipt = deal.get("receipt") or {}
        decision("review", "review", policy_ver=ver, ref={"order": order_id, "ledger": settled.get("ledger")},
                 inputs={"price": receipt.get("price"), "receipt_item": receipt.get("item"),
                         "receipt_unit": receipt.get("unit"),
                         **{k: settled.get(k) for k in ("shop", "sku", "unit", "grade", "claim", "claim_honest",
                                                        "value", "warranty_hours", "expires_at")}},
                 memory_refs=[row["id"]] if row else [], rules=["§4.5"], choice={"stars": stars}, why=review)
    except Exception as e:
        hook_log("review", f"error {type(e).__name__}: {e}")
        return 1
    print(json.dumps({"stars": stars, "review": review}, ensure_ascii=False), flush=True)
    try:                                   # D11: a forum error never changes the stars or the exit code
        forum.maybe_post(args.deal.parent, deal, pol, time.time(), policy_ver=ver)
    except Exception as e:
        hook_log("review", f"forum error {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
