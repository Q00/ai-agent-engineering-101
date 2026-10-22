"""Harness-driven buyer with frozen strategy policy and an atomic local purchase ledger."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import re
import time
import uuid

import httpx

from catalog import CATEGORY_TERMS
from evidence import EvidenceError, Ledger, inspect_untrusted, validate_buyer_decision, verify_receipt
from harness import Harness
from policy import StrategyPolicy, strategy_instruction
from protocol import A2AClient, ProtocolError, receipt_from
from quote_normalization import contract_issues, normalize_quote
from tracing import Trace


_PENDING_TASK_STATES = {"TASK_STATE_SUBMITTED", "TASK_STATE_WORKING"}
_RECOVERY_MAX_READS = 3
_RECOVERY_SECONDS = 2.0
_RECOVERY_READ_SECONDS = 0.6


def _transient_transport_failure(exc):
    """Application/protocol errors never authorize retrying a purchase."""
    if isinstance(exc, (TimeoutError, httpx.TransportError)):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {
        408, 429, 500, 502, 503, 504}


def _recover_task(client, task_id, *, trace, deadline, failure=None, allow_input_required=False,
                  receipt_evidence=None):
    """Reconcile only the known task using a short, read-only polling window.

    INPUT_REQUIRED from a recovery read may be a stale pre-offer snapshot. It
    cannot prove the authorized bid was rejected, so it never releases funds or
    starts another negotiation. Terminal outcomes still pass the ordinary
    receipt/conflicting-artifact checks in negotiate().
    """
    # Require an implemented method, not a dynamically fabricated Mock attribute.
    if not callable(getattr(type(client), "get_task", None)):
        if failure is not None:
            raise failure
        raise ProtocolError("Pending task outcome unresolved: GetTask unavailable")
    recovery_deadline = min(deadline, time.monotonic() + _RECOVERY_SECONDS)
    cause = type(failure).__name__ if failure is not None else "pending_task"
    trace.write("task_recovery_started", task_id=task_id, cause=cause,
                max_reads=_RECOVERY_MAX_READS, read_only=True)
    last_error = failure
    receipt_evidence = receipt_evidence if receipt_evidence is not None else []
    for attempt in range(1, _RECOVERY_MAX_READS + 1):
        remaining = recovery_deadline - time.monotonic()
        if remaining <= 0:
            break
        started = time.monotonic()
        try:
            task = client.get_task(task_id, timeout=min(_RECOVERY_READ_SECONDS, remaining))
        except Exception as exc:
            last_error = exc
            trace.write("task_recovery_read_failed", task_id=task_id, attempt=attempt,
                        detail=str(exc), transient=_transient_transport_failure(exc),
                        response_seconds=round(time.monotonic() - started, 3))
            if not _transient_transport_failure(exc):
                raise
        else:
            if not isinstance(task, dict) or task.get("id") != task_id:
                raise ProtocolError("Task recovery changed the authorized task ID")
            status = task.get("status")
            state = status.get("state") if isinstance(status, dict) else None
            trace.write("task_recovery_observed", task_id=task_id, attempt=attempt, state=state,
                        response_seconds=round(time.monotonic() - started, 3))
            if state in {"TASK_STATE_COMPLETED", "TASK_STATE_REJECTED",
                         "TASK_STATE_CANCELED", "TASK_STATE_FAILED"}:
                return task
            if state == "TASK_STATE_INPUT_REQUIRED" and allow_input_required:
                return task
            if state not in _PENDING_TASK_STATES:
                raise ProtocolError(f"Task recovery outcome unresolved: {state}")
            if _has_terminal_receipt_evidence(task):
                receipt_evidence.append(deepcopy(task))
            if inspect_untrusted(json.dumps(task, ensure_ascii=False)):
                raise ProtocolError("Pending task contains untrusted instructions; recovery stopped")
        if attempt < _RECOVERY_MAX_READS:
            remaining = recovery_deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(0.1, remaining))
    trace.write("task_recovery_unresolved", task_id=task_id, cause=cause)
    raise TimeoutError("Task outcome unresolved after bounded read-only recovery") from last_error


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


def _prior_receipt_issues(receipt, observed_tasks):
    """A final receipt may fill missing fields, but cannot erase prior evidence."""
    issues = []
    for observed in observed_tasks:
        prior_data = []
        for artifact in observed.get("artifacts", []):
            if not isinstance(artifact, dict):
                continue
            named_receipt = isinstance(artifact.get("name"), str) and artifact["name"].strip().casefold() == "receipt"
            parts = artifact.get("parts", [])
            if not isinstance(parts, list):
                continue
            prior_data.extend(part["data"] for part in parts if isinstance(part, dict)
                and isinstance(part.get("data"), dict) and (named_receipt or "price" in part["data"]))
        if len(prior_data) > 1:
            issues.append("prior_ambiguous_receipt_evidence")
        if any(key not in receipt or receipt[key] != value
               for data in prior_data for key, value in data.items()):
            issues.append("prior_receipt_changed")
    return list(dict.fromkeys(issues))


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
    response_seconds = []
    receipt_evidence = []
    task_id = None
    expected_item, quoted_item, expected_quote_id = requested_item_id or item, None, None
    try:
        sent_at = time.monotonic()
        task = client.send(f"I want to buy a {item}")
        response_seconds.append(round(time.monotonic() - sent_at, 3))
        task_id = task.get("id")
        if not task_id:
            raise ProtocolError("Seller did not return a task ID")
        # Eight decisions can produce eight replies. Drain the final reply's
        # terminal evidence before applying the decision limit or deadline.
        for round_number in range(1, 10):
            if task.get("id") != task_id:
                raise ProtocolError("Counterpart changed task ID during negotiation")
            state = task.get("status", {}).get("state")
            if state in _PENDING_TASK_STATES:
                if _has_terminal_receipt_evidence(task):
                    receipt_evidence.append(deepcopy(task))
                if inspect_untrusted(json.dumps(task, ensure_ascii=False)):
                    raise ProtocolError("Pending task contains untrusted instructions; recovery stopped")
                task = _recover_task(client, task_id, trace=trace, deadline=deadline,
                                     allow_input_required=not reserved, receipt_evidence=receipt_evidence)
                state = task.get("status", {}).get("state")
            if state == "TASK_STATE_COMPLETED":
                receipt = receipt_from(task)
                issues = verify_receipt(receipt, item=expected_item, budget=budget,
                                        expected_price=expected_price, task_id=task_id,
                                        expected_quote_id=expected_quote_id)
                issues.extend(contract_issues(receipt))
                issues.extend(_prior_receipt_issues(receipt, receipt_evidence))
                trace.write("receipt_checked", issues=issues, receipt=receipt)
                if issues:
                    if reserved:
                        ledger.mark_disputed(purchase_id, "; ".join(issues))
                    return {"status": "disputed", "issues": issues, "receipt": receipt}
                if not reserved:
                    raise EvidenceError("Unsolicited completed purchase without our authorized offer")
                ledger.commit(purchase_id, receipt)
                trace.write("purchase_receipt_matched", receipt=receipt, ledger=ledger.snapshot(),
                            signature_verified=False, market_verified=False)
                return {"status": "completed", "receipt": receipt, "offer_count": len(history),
                        "response_seconds": response_seconds, "market_verified": False}
            if state in ("TASK_STATE_REJECTED", "TASK_STATE_CANCELED", "TASK_STATE_FAILED"):
                if _has_terminal_receipt_evidence(task) or receipt_evidence:
                    # A peer may fail after publishing a purchase artifact. Its
                    # terminal label cannot prove the prior offer was unspent.
                    if reserved:
                        ledger.mark_disputed(purchase_id, "terminal_receipt_conflict")
                    trace.write("terminal_receipt_conflict", state=state, reserved=reserved,
                                artifacts=task.get("artifacts", []),
                                prior_artifacts=[observed.get("artifacts", []) for observed in receipt_evidence])
                    return {"status": "disputed", "state": state,
                            "issues": ["terminal_receipt_conflict"]}
                if reserved:
                    ledger.release(purchase_id)
                    reserved = False
                trace.write("walked_away", state=state)
                return {"status": "no_deal", "state": state, "offer_count": len(history),
                        "response_seconds": response_seconds}
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
                return {"status": "no_deal", "reason": decision["reason"], "offer_count": len(history),
                        "response_seconds": response_seconds}
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
            sent_at = time.monotonic()
            try:
                task = client.send(json.dumps(price, allow_nan=False), task_id, **send_options)
            except Exception as exc:
                if not _transient_transport_failure(exc):
                    raise
                task = _recover_task(client, task_id, trace=trace, deadline=deadline, failure=exc,
                                     receipt_evidence=receipt_evidence)
            response_seconds.append(round(time.monotonic() - sent_at, 3))
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
