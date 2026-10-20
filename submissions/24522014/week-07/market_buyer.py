"""LAB 1 Step 2: select a shop, reuse the week-06 buyer, remember and review."""

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import ssl
import sys
from uuid import uuid4

import httpx

from buyer import BuyerError, BuyerTransport, run_autonomous
from buyer_memory import choose_shop, load_memory, remember, RECENT_COUNT
from shop_catalog import load_shops

ROOT = Path(__file__).resolve().parent


class ReceiptProblem(BuyerError):
    def __init__(self, message, kind="invalid_receipt"):
        super().__init__(message)
        self.kind = kind


def check_receipt(receipt, *, item, budget, task, agreed_price):
    if not isinstance(receipt, dict):
        raise ReceiptProblem("Completed task has no unique receipt")
    price = receipt.get("price")
    if type(price) not in (int, float) or not math.isfinite(price) or price <= 0 or int(price) != price:
        raise ReceiptProblem("Receipt price must be a positive whole number")
    if agreed_price is not None and price != agreed_price:
        raise ReceiptProblem(f"Agreed {agreed_price}, receipt says {price}.", "receipt_mismatch")
    if receipt.get("item") is not None and receipt["item"] != item:
        raise ReceiptProblem("Receipt names a different item.", "receipt_mismatch")
    if receipt.get("task") is not None and receipt["task"] != task:
        raise ReceiptProblem("Receipt names a different task.", "receipt_mismatch")
    if agreed_price is None or price > budget or receipt.get("item") != item or receipt.get("task") != task:
        raise ReceiptProblem("Receipt cannot be matched to an in-budget agreed purchase")


class MarketTransport(BuyerTransport):
    """Retain the original artifact even when the inherited validation rejects it."""
    last_offer = None
    raw_receipt = None

    def __init__(self, *args, **kwargs):
        # Use OS-trusted CAs explicitly: trust_env=False ignores SSL_CERT_FILE.
        super().__init__(*args, verify=ssl.create_default_context(), **kwargs)

    def offer(self, price):
        if type(price) is not int or not 0 < price <= self.budget:
            raise BuyerError("Offer must be a positive integer within budget")
        self.last_offer = price
        return super().offer(price)

    def _rpc(self, method, params):
        try:
            return super()._rpc(method, params)
        finally:
            if self.state == "TASK_STATE_COMPLETED":
                receipts = [part["data"] for artifact in self.task.get("artifacts", [])
                            for part in artifact.get("parts", [])
                            if isinstance(part.get("data"), dict) and "price" in part["data"]]
                self.raw_receipt = receipts[0] if len(receipts) == 1 else None
                check_receipt(self.raw_receipt, item=self.item, budget=self.budget,
                              task=self.task_id, agreed_price=self.last_offer)


def error_kind(error):
    if isinstance(error, ReceiptProblem):
        return error.kind
    cause = error
    while cause is not None:
        if isinstance(cause, httpx.TransportError):
            return "transport_error"
        cause = cause.__cause__
    return "agent_error"


def trade(shop, item, budget, *, mode="claude", transport=None):
    client = MarketTransport(shop["url"], item, budget, transport=transport)
    error = None
    try:
        with client:
            summary = run_autonomous(client, mode)
        outcome = "purchased" if summary["outcome"] == "purchased" else "no_deal"
    except (BuyerError, OSError, ValueError) as exc:
        outcome, error = error_kind(exc), str(exc)
    receipt = client.raw_receipt
    if outcome == "purchased":
        stars, review = 5, f"Bought {item} at agreed price {receipt['price']}, within budget {budget}; receipt matches item and task."
    elif outcome == "receipt_mismatch":
        stars, review = 1, error
    elif outcome == "invalid_receipt":
        stars, review = 2, error
    elif outcome == "transport_error":
        stars, review = 3, "Connection failed; purchase not confirmed. No dishonesty inferred."
    elif outcome == "no_deal":
        stars, review = 3, f"No deal within budget {budget}; no dishonesty inferred."
    else:
        stars, review = 3, "Buyer could not complete the trade; no seller dishonesty inferred."
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), "shop_name": shop["name"],
              "url_at_time": shop["url"], "item": item, "budget": budget, "outcome": outcome,
              "price": receipt.get("price") if receipt else None, "agreed_price": client.last_offer,
              "task": client.task_id, "receipt": receipt, "error": error,
              "verification": "local_receipt_consistent" if outcome == "purchased" else "not_confirmed",
              "signature_verification": "left_to_market_kit", "stars": stars, "review": review,
              "buyer_log_dir": str(client.log_dir)}
    result = {"shop": shop["url"], "receipt": receipt if outcome == "purchased" else None,
              "stars": stars, "review": review}
    return result, record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shops", type=Path, required=True)
    parser.add_argument("--item", choices=("lamp", "chair", "bookshelf"), required=True)
    parser.add_argument("--budget", type=int, required=True)
    parser.add_argument("--memory", type=Path, default=ROOT / ".market/buyer-memory.jsonl")
    parser.add_argument("--memory-window", type=int, choices=(3, 5, 10, 20), default=RECENT_COUNT)
    parser.add_argument("--mode", choices=("claude", "codex"), default="claude")
    args = parser.parse_args()
    if args.budget <= 0:
        parser.error("Budget must be a positive integer")
    try:
        shops, memory = load_shops(args.shops), load_memory(args.memory)
        shop, reason, evidence = choose_shop(shops, memory, args.item, args.budget,
                                              recent_count=args.memory_window)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Cannot load selection inputs: {error}\n")
    print(f"[choose] {reason}", flush=True)
    if shop:
        result, record = trade(shop, args.item, args.budget, mode=args.mode)
        record["selection_reason"] = reason
        remember(args.memory, record)
    else:
        result = {"shop": "", "receipt": None, "stars": 3, "review": reason}
        record = {"outcome": "no_eligible_shop", "selection_reason": reason}
    log_dir = ROOT / "logs/market-buyer"
    log_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
    (log_dir / f"{run_id}.json").write_text(
        json.dumps({"record": record, "selection_evidence": evidence, "result": result},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
