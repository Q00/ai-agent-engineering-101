"""Harness-driven buyer with frozen strategy policy and an atomic local purchase ledger."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import re
import time
import uuid

from catalog import CATEGORY_TERMS
from evidence import EvidenceError, Ledger, inspect_untrusted, validate_buyer_decision, verify_receipt
from harness import Harness
from policy import StrategyPolicy, strategy_instruction
from protocol import A2AClient, ProtocolError, receipt_from
from quote_normalization import contract_issues, normalize_quote
from tracing import Trace


def _has_terminal_receipt_evidence(task):
    """Detect purchase evidence that contradicts an explicit no-deal state.

    A DataPart containing price is receipt-shaped even when its value is invalid.
    An artifact named receipt also counts when partial or empty. Other diagnostic
    artifacts do not turn an otherwise explicit failure into a possible purchase.
    """
    artifacts = task.get("artifacts", [])
    if not isinstance(artifacts, list):
        return False
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        name = artifact.get("name")
        if isinstance(name, str) and name.strip().casefold() == "receipt":
            return True
        parts = artifact.get("parts", [])
        if isinstance(parts, list) and any(
                isinstance(part, dict) and isinstance(part.get("data"), dict) and "price" in part["data"]
                for part in parts):
            return True
    return False


def negotiate(url, item, budget, *, ledger=None, item_key=None, policy=None, trace=None,
              timeout=240, expected_item_id=None, coordination_context=None):
    if not isinstance(item, str) or not item.strip():
        raise EvidenceError("Requested item must be nonempty text")
    if coordination_context is not None and not isinstance(coordination_context, dict):
        raise EvidenceError("Coordination context must be a factual object")
    if expected_item_id is not None:
        if not isinstance(expected_item_id, str) or not expected_item_id.strip():
            raise EvidenceError("Expected item ID must be nonempty text")
        requested_item_id = expected_item_id
    else:
        normalized = item.strip().lower()
        numbered = re.fullmatch(r"([a-z]+)-\d+", normalized)
        requested_item_id = normalized if (normalized in CATEGORY_TERMS or
            (numbered is not None and numbered.group(1) in CATEGORY_TERMS)) else None
    trace = trace or Trace("buyer")
    ledger = ledger or Ledger(budget)
    policy = policy or StrategyPolicy()
    trace.write("policy_loaded", policy_sha256=policy.sha256, policy_version=policy.version)
    item_key = item_key or item
    harness = Harness("buyer", trace)
    observer = Harness("quote_observer", trace)
    deadline = time.monotonic() + timeout
    client = A2AClient(url, trace, deadline=deadline)
    purchase_id = uuid.uuid4().hex
    reserved = False
    history, expected_price = [], None
    task_id = None
    expected_item, quoted_item, expected_quote_id = requested_item_id or item, None, None
    try:
        task = client.send(f"I want to buy a {item}")
        task_id = task.get("id")
        if not task_id:
            raise ProtocolError("Seller did not return a task ID")
        # Eight decisions can produce eight replies. Drain the final reply's
        # terminal evidence before applying the decision limit or deadline.
        for round_number in range(1, 10):
            if task.get("id") != task_id:
                raise ProtocolError("Counterpart changed task ID during negotiation")
            state = task.get("status", {}).get("state")
            if state == "TASK_STATE_COMPLETED":
                receipt = receipt_from(task)
                issues = verify_receipt(receipt, item=expected_item, budget=budget,
                                        expected_price=expected_price, task_id=task_id,
                                        expected_quote_id=expected_quote_id)
                issues.extend(contract_issues(receipt))
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
                if _has_terminal_receipt_evidence(task):
                    # A peer may fail after publishing a purchase artifact. Its
                    # terminal label cannot prove the prior offer was unspent.
                    if reserved:
                        ledger.mark_disputed(purchase_id, "terminal_receipt_conflict")
                    trace.write("terminal_receipt_conflict", state=state, reserved=reserved,
                                artifacts=task.get("artifacts", []))
                    return {"status": "disputed", "state": state,
                            "issues": ["terminal_receipt_conflict"]}
                if reserved:
                    ledger.release(purchase_id)
                    reserved = False
                trace.write("walked_away", state=state)
                return {"status": "no_deal", "state": state}
            if state != "TASK_STATE_INPUT_REQUIRED":
                raise ProtocolError(f"Unsupported task state: {state}")
            if round_number > 8:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Buyer negotiation deadline reached")
            observation = normalize_quote(task, observer=observer, trace=trace, deadline=deadline,
                context={"request": item, "requested_item_id": requested_item_id,
                         "task_id": task_id, "round": round_number})
            unit_issues = contract_issues({key: observation[key] for key in ("currency", "quantity")
                                           if observation[key] is not None})
            if unit_issues:
                raise EvidenceError("Quote violates the credits/quantity-one contract: " + ", ".join(unit_issues))
            text, metadata = observation["remote_message"], observation["remote_metadata"]
            expected_quote_id = observation["quote_id"]
            flags = inspect_untrusted(json.dumps({"text": text, "metadata": metadata,
                "task_metadata": observation["remote_task_metadata"],
                "data": observation["remote_data"]}, ensure_ascii=False))
            ask = observation["ask"]
            remote_item = observation["item_id"]
            if remote_item is not None:
                if not isinstance(remote_item, str) or not remote_item:
                    raise EvidenceError("Invalid quoted item ID")
                if quoted_item is not None and remote_item != quoted_item:
                    raise EvidenceError("Seller changed the quoted product during negotiation")
                if requested_item_id is not None and remote_item != requested_item_id:
                    raise EvidenceError("Seller substituted a different explicitly requested catalog item")
                quoted_item = remote_item
                expected_item = quoted_item
            # Public observations enter the frozen policy only after format normalization.
            context = {"role": "buyer", "item": expected_item, "request": item, "budget": budget,
                       "remote_message": text, "remote_metadata": metadata, "ask": ask,
                       "remote_data": observation["remote_data"], "quote_observation": observation,
                       "round": round_number, "remaining_rounds": 9 - round_number,
                       "task_id": task_id, "untrusted_flags": flags,
                       "flags": flags,
                       "remaining_seconds": round(deadline - time.monotonic()), "history_summary": history[-8:]}
            if coordination_context is not None:
                # Coordinator observations never replace the slot's authorized budget.
                context["coordination"] = deepcopy(coordination_context)
            strategy = policy.choose("buyer", context)
            def validate_decision(output):
                validated = validate_buyer_decision(output, budget=budget, ask=ask)
                if validated["action"] == "accept" and ask is None:
                    raise EvidenceError("Cannot accept without an observed asking price; offer or walk away instead")
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
            history.append({"seller": text, "metadata": metadata, "ask": ask,
                            "quote_observation": {key: observation[key] for key in (
                                "ask", "item_id", "quote_id", "currency", "quantity", "source")},
                            "action": decision["action"], "price": price})
            send_options = {}
            if coordination_context is not None and coordination_context.get("purpose") == "shopping_list":
                # Only an explicit model-selected public stance can leave the
                # shopping coordinator. Price equality never implies a stance.
                stance = decision.get("stance")
                if stance in ("negotiable", "firm"):
                    send_options["stance"] = stance
            task = client.send(json.dumps(price, allow_nan=False), task_id, **send_options)
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
    parser.add_argument("--expected-item-id", help="Require an exact external catalog ID, including arbitrary SKU formats")
    parser.add_argument("--policy")
    parser.add_argument("--log-dir")
    args = parser.parse_args()
    trace = Trace("buyer", args.log_dir)
    result = negotiate(args.url, args.item, args.budget, policy=StrategyPolicy(args.policy), trace=trace,
                       expected_item_id=args.expected_item_id)
    print(json.dumps(result, ensure_ascii=False), flush=True)
