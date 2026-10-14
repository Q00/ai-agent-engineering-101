"""Harness-driven buyer with frozen strategy policy and an atomic local purchase ledger."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
import uuid

from evidence import EvidenceError, Ledger, inspect_untrusted, validate_buyer_decision, verify_receipt
from harness import Harness
from policy import StrategyPolicy, strategy_instruction
from protocol import A2AClient, ProtocolError, receipt_from, task_metadata, task_text
from tracing import Trace


def negotiate(url, item, budget, *, ledger=None, item_key=None, policy=None, trace=None, timeout=240):
    trace = trace or Trace("buyer")
    ledger = ledger or Ledger(budget)
    policy = policy or StrategyPolicy()
    trace.write("policy_loaded", policy_sha256=policy.sha256, policy_version=policy.version)
    item_key = item_key or item
    harness = Harness("buyer", trace)
    deadline = time.monotonic() + timeout
    client = A2AClient(url, trace, deadline=deadline)
    purchase_id = uuid.uuid4().hex
    reserved = False
    history, expected_price = [], None
    task_id = None
    expected_item, quoted_item, expected_quote_id = item, None, None
    try:
        task = client.send(f"I want to buy a {item}")
        task_id = task.get("id")
        if not task_id:
            raise ProtocolError("Seller did not return a task ID")
        for round_number in range(1, 9):
            if time.monotonic() >= deadline:
                raise TimeoutError("Buyer negotiation deadline reached")
            if task.get("id") != task_id:
                raise ProtocolError("Counterpart changed task ID during negotiation")
            state = task.get("status", {}).get("state")
            if state == "TASK_STATE_COMPLETED":
                receipt = receipt_from(task)
                issues = verify_receipt(receipt, item=expected_item, budget=budget,
                                        expected_price=expected_price, task_id=task_id,
                                        expected_quote_id=expected_quote_id)
                trace.write("receipt_checked", issues=issues, receipt=receipt)
                if issues:
                    if reserved:
                        ledger.mark_disputed(purchase_id, "; ".join(issues))
                    return {"status": "disputed", "issues": issues, "receipt": receipt}
                if not reserved:
                    raise EvidenceError("Unsolicited completed purchase without our authorized offer")
                ledger.commit(purchase_id, receipt)
                trace.write("purchase_completed", receipt=receipt, ledger=ledger.snapshot())
                return {"status": "completed", "receipt": receipt}
            if state in ("TASK_STATE_REJECTED", "TASK_STATE_CANCELED", "TASK_STATE_FAILED"):
                if reserved:
                    ledger.release(purchase_id)
                    reserved = False
                trace.write("walked_away", state=state)
                return {"status": "no_deal", "state": state}
            if state != "TASK_STATE_INPUT_REQUIRED":
                raise ProtocolError(f"Unsupported task state: {state}")
            text, metadata = task_text(task), task_metadata(task)
            expected_quote_id = metadata.get("quoteId")
            flags = inspect_untrusted(text)
            ask = metadata.get("ask")
            remote_item = metadata.get("itemId")
            if remote_item is not None:
                if not isinstance(remote_item, str) or not remote_item:
                    raise EvidenceError("Invalid quoted item ID")
                if quoted_item is not None and remote_item != quoted_item:
                    raise EvidenceError("Seller changed the quoted product during negotiation")
                quoted_item = remote_item
                if item in ("lamp", "chair", "bookshelf") and quoted_item != item:
                    raise EvidenceError("Seller substituted a different explicitly requested catalog item")
                expected_item = quoted_item
            # A2A does not define price semantics. Our harness interprets text if no structured quote exists.
            context = {"role": "buyer", "item": expected_item, "request": item, "budget": budget,
                       "remote_message": text, "remote_metadata": metadata, "ask": ask,
                       "round": round_number, "task_id": task_id, "untrusted_flags": flags,
                       "flags": flags,
                       "remaining_seconds": round(deadline - time.monotonic()), "history_summary": history[-8:]}
            strategy = policy.choose("buyer", context)
            def validate_decision(output):
                validated = validate_buyer_decision(output, budget=budget, ask=ask)
                if validated["item_id"] != expected_item:
                    raise EvidenceError("Buyer decision must keep the requested quoted item ID")
                return validated
            decision = harness.decide(context, strategy=strategy,
                instruction=strategy_instruction("buyer", strategy), deadline=deadline,
                validator=validate_decision)
            if decision["action"] == "walk_away":
                if reserved:
                    ledger.release(purchase_id)
                    reserved = False
                trace.write("walked_away", reason=decision["reason"])
                return {"status": "no_deal", "reason": decision["reason"]}
            price = decision["price"]
            if reserved:
                ledger.release(purchase_id)
                reserved = False
            purchase_id = uuid.uuid4().hex
            # Reserve before every offer: even a counteroffer can be accepted immediately by a seller.
            ledger.reserve(item_key, price, purchase_id, expected_item=expected_item,
                           seller_origin=client.base, expected_quote_id=expected_quote_id)
            reserved, expected_price = True, price
            history.append({"seller": text, "metadata": metadata, "action": decision["action"], "price": price})
            task = client.send(json.dumps(price, allow_nan=False), task_id)
        if reserved:
            ledger.release(purchase_id)
            reserved = False
        trace.write("walked_away", reason="round limit")
        return {"status": "no_deal", "reason": "round limit"}
    except Exception as exc:
        # A lost response may conceal a completed sale. Do not release funds and buy again blindly.
        if reserved:
            ledger.mark_disputed(purchase_id, str(exc))
        trace.write("buyer_failed", detail=str(exc), task_id=task_id)
        raise
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", required=True, type=float)
    parser.add_argument("--policy")
    parser.add_argument("--log-dir")
    args = parser.parse_args()
    trace = Trace("buyer", args.log_dir)
    result = negotiate(args.url, args.item, args.budget, policy=StrategyPolicy(args.policy), trace=trace)
    print(json.dumps(result, ensure_ascii=False), flush=True)
