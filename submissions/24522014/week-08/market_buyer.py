"""R3 buyer: sparse seller-profit research with verified settlement feedback."""

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import ssl
import sys
import time
from uuid import uuid4

import httpx

from buyer import BuyerError, BuyerTransport, run_autonomous, seller_metadata
from buyer_memory import load_memory, remember, RECENT_COUNT
from shop_catalog import load_shops
from r3_policy import buyer_accept, load_policy
from settlement_memory import payment_outcomes, reconcile
from market_intel import fresh
from buyer_support import prepare as prepare_support, retry_settings, should_retry
import discount_floor
import model_router

ROOT = Path(__file__).resolve().parent
CATEGORY_ONLY = "receipt_category_only"
CANCEL_WAIT_SECONDS = 10


class ReceiptProblem(BuyerError):
    def __init__(self, message, kind="invalid_receipt"):
        super().__init__(message)
        self.kind = kind


def is_category_receipt(receipt, *, budget, task, agreed_price, order_item):
    """The contract allows a receipt to name the order's item instead of a SKU, but then nothing shows which SKU we would get
    and the kit does not compare it with the one we priced. Honest on every other point, it is not paid and not a mismatch."""
    return (isinstance(order_item, str) and bool(order_item) and receipt.get("item") == order_item and receipt.get("task") == task
            and agreed_price is not None and receipt.get("price") == agreed_price and receipt["price"] <= budget)


def receipt_format(receipt, item, order_item):
    """How a receipt named what it sold, for the funnel."""
    named = receipt.get("item") if isinstance(receipt, dict) else None
    if named is None:
        return None
    if named == item:
        return "sku"
    return "order_item" if isinstance(order_item, str) and order_item and named == order_item else "other"


def check_receipt(receipt, *, item, budget, task, agreed_price, order_item=None):
    if not isinstance(receipt, dict):
        raise ReceiptProblem("Completed task has no unique receipt")
    price = receipt.get("price")
    if type(price) not in (int, float) or not math.isfinite(price) or price <= 0 or int(price) != price:
        raise ReceiptProblem("Receipt price must be a positive whole number")
    if agreed_price is not None and price != agreed_price:
        raise ReceiptProblem(f"Agreed {agreed_price}, receipt says {price}.", "receipt_mismatch")
    if receipt.get("item") is not None and receipt["item"] != item:
        if is_category_receipt(receipt, budget=budget, task=task, agreed_price=agreed_price, order_item=order_item):
            raise ReceiptProblem("Receipt names the order's category, not a SKU.", CATEGORY_ONLY)
        raise ReceiptProblem("Receipt names a different item.", "receipt_mismatch")
    if receipt.get("task") is not None and receipt["task"] != task:
        raise ReceiptProblem("Receipt names a different task.", "receipt_mismatch")
    if agreed_price is None or price > budget or receipt.get("item") != item or receipt.get("task") != task:
        raise ReceiptProblem("Receipt cannot be matched to an in-budget agreed purchase")


class MarketTransport(BuyerTransport):
    """Retain the original artifact even when the inherited validation rejects it."""
    last_offer = None
    raw_receipt = None
    requirements = None
    order_item = None

    def open(self):
        # SKU only: a catalog name carries category words, and shops then handed over another item of that category.
        if not self.requirements:
            return super().open()
        if self.task is not None:
            raise BuyerError("This buyer already has a task")
        text = f"I want to buy {self.item}. Please include a market certificate. "
        if self.requirements.get("warranty_hours"):
            text += f"I require {self.requirements['warranty_hours']} hours of warranty in the checkout. "
        else:
            text += "If available, please offer a 6-hour goods warranty; no warranty is assumed. "
        return self._rpc("SendMessage", {"message": {"role": "ROLE_USER", "messageId": str(uuid4()),
                         "parts": [{"text": text}]}})

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
                              task=self.task_id, agreed_price=self.last_offer, order_item=self.order_item)


def release_category_task(client):
    """Ask the shop to close a finished task so its signed unit is not left waiting. A shop may refuse, and that changes nothing."""
    outcome = {"attempted": True, "result": "not_confirmed", "error": None}
    try:
        client.deadline = min(client.deadline, time.monotonic() + CANCEL_WAIT_SECONDS)
        client._rpc("CancelTask", {"id": client.task_id})
        if client.state == "TASK_STATE_CANCELED":
            outcome["result"] = "canceled"
    except Exception as error:
        outcome.update(result="failed", error=str(error)[:200] or type(error).__name__)
    outcome["state"] = client.state
    return outcome


def error_kind(error):
    if isinstance(error, ReceiptProblem):
        return error.kind
    cause = error
    while cause is not None:
        if isinstance(cause, httpx.TransportError):
            return "transport_error"
        if isinstance(cause, httpx.HTTPStatusError) and (
            cause.response.status_code >= 500 or cause.response.status_code in (408, 429)
        ):
            return "transport_error"
        cause = cause.__cause__
    return "agent_error"


def failure_reason(outcome, negotiation, decisions):
    """Why this attempt produced no receipt. Whether a receipt was paid is decided later from payments.jsonl."""
    if outcome == "purchased":
        return None
    last = decisions[-1] if decisions else None
    if negotiation == "walked_away" and last:
        return last["reason_code"]
    if outcome == "no_deal":
        return negotiation or outcome
    if outcome == "agent_error" and last and last["action"] is None:
        return last["reason_code"]
    return outcome


def trade(shop, item, budget, *, mode="claude", transport=None, requirements=None, decision_context=None, max_turns=6, fast_rules=False, model_gate=None, accept_conditions=None, over_budget_counter=None, catalog_list_price=None, order_item=None, price_reading=None, reference_relief=None, discount_limit=None):
    client = MarketTransport(shop["url"], item, budget, transport=transport, timeout=180)
    client.discount_limit = discount_limit
    client.requirements = requirements
    client.order_item = order_item
    client.accept_conditions = accept_conditions
    client.strategy_prompt = ROOT / "prompts/buyer-r3.txt"
    client.r3_context = decision_context
    if fast_rules:
        from buyer_fast import decision
        client.price_reading = price_reading
        client.reference_relief = reference_relief
        client.rule_decision = lambda text: decision(text, client.budget, client.last_offer, seller_metadata(client.task) if price_reading else None, price_reading)
        cached = []
        def allow_model():
            if not cached:
                answer = model_gate() if model_gate else "model_gate_missing"
                client.model_gate_reason = {True: "reserved", False: "model_gate_denied"}.get(answer, answer)
                cached.append(client.model_gate_reason in ("reserved", "gate_disabled"))
            return cached[0]
        client.model_gate = allow_model
        client.over_budget_counter = over_budget_counter
        client.catalog_list_price = catalog_list_price
    error = negotiation = release = None
    try:
        with client:
            try:
                summary = run_autonomous(client, mode, max_turns=max_turns)
            except ReceiptProblem as problem:
                if problem.kind == CATEGORY_ONLY:
                    held = client.raw_receipt
                    release = release_category_task(client)
                    client.raw_receipt = held
                raise problem
        negotiation = summary["outcome"]
        outcome = "purchased" if negotiation == "purchased" else "no_deal"
    except (BuyerError, OSError, ValueError) as exc:
        outcome, error = error_kind(exc), str(exc)
    receipt = client.raw_receipt
    if outcome == "purchased":
        stars, review = 5, f"Bought {item} at agreed price {receipt['price']}, within budget {budget}; receipt matches item and task."
    elif outcome == "receipt_mismatch":
        stars, review = 1, error
    elif outcome == CATEGORY_ONLY:
        stars, review = 3, "Receipt names no SKU, so this buyer did not proceed; no dishonesty inferred."
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
              "negotiation_outcome": negotiation, "decision_trail": client.decisions,
              "request_format": "sku_only", "receipt_format": receipt_format(receipt, item, order_item), "category_receipt_release": release,
              "reference_relief": getattr(client, "reference_relief_taken", None),
              "refused_at_open": client.state == "TASK_STATE_REJECTED" and not client.decisions,
              "failure_reason": failure_reason(outcome, negotiation, client.decisions),
              "buyer_log_dir": str(client.log_dir)}
    result = {"shop": shop["url"], "receipt": receipt if outcome == "purchased" else None,
              "stars": stars, "review": review}
    return result, record


def model_gate(market_dir, order_id, reservation):
    """Availability goes first, so a model that is off never takes a place in the hourly and daily limits."""
    allowed, reason = model_router.available(role="buyer")
    return reservation(market_dir, order_id) if allowed else f"model_breaker_{reason}"


def select_attempt(md, shops, memory, catalog, budget, snapshot, order, retry_of=None):
    """Choose a shop for one attempt; each attempt starts from the order's own accept conditions."""
    accept_conditions = buyer_accept(order)
    shop, selected_item, trade_budget, reason, evidence, support_decision = prepare_support(
        md, shops, memory, catalog, budget, load_policy()["buying"], None, snapshot, order, retry_of=retry_of)
    selected_evidence = next((e for e in evidence if shop and e["shop"] == shop["name"] and e["sku"] == selected_item), {})
    if selected_evidence.get("warranty_required"):
        accept_conditions["warranty_hours"] = selected_evidence["warranty_required"]
    return {"shop": shop, "item": selected_item, "budget": trade_budget, "reason": reason, "evidence": evidence,
            "decision": support_decision, "selected": selected_evidence, "accept": accept_conditions}


def trade_attempt(args, order, catalog, pick):
    shop, selected_item, accept_conditions, selected_evidence, support_decision = pick["shop"], pick["item"], pick["accept"], pick["selected"], pick["decision"]
    trade_options = {"mode": args.mode, "decision_context": selected_evidence,
                     "max_turns": support_decision["max_turns"], "accept_conditions": accept_conditions, "order_item": order.get("item"),
                     "discount_limit": {"list_price": (catalog.get(selected_item) or {}).get("list_price"), "kind": order.get("kind"), "covered": bool(selected_evidence.get("warranty_required")),
                                        "grades": accept_conditions.get("grades"), "cap_without_floor": selected_evidence.get("cap_without_floor"), "settings": discount_floor.load()}}
    relief_token = None
    if support_decision.get("fast_rules"):
        from buyer_counter import load_settings
        from buyer_fast import load_reading
        from buyer_support import model_reservation
        import reference_relief
        relief_token = reference_relief.active(args.order.parent, ROOT)
        # One model reservation per order: a retry finds it taken and stays on the rules.
        trade_options.update(fast_rules=True, model_gate=lambda: model_gate(args.order.parent, order["order"], model_reservation),
                             over_budget_counter=load_settings(), price_reading=load_reading(),
                             reference_relief={"order_budget": args.budget, "kind": order.get("kind"), "catalog_kind": (catalog.get(selected_item) or {}).get("kind")} if relief_token else None,
                             catalog_list_price=(catalog.get(selected_item) or {}).get("list_price"))
    if order.get("kind") == "goods":
        trade_options["requirements"] = accept_conditions
    result, record = trade(shop, selected_item, pick["budget"], **trade_options)
    record["round"] = 3
    record["order"] = order.get("order")
    record["order_budget"] = args.budget
    record["category"] = order.get("category", args.item)
    record["accept_conditions"] = accept_conditions
    if record.get("reference_relief"):
        import reference_relief
        reference_relief.note_deal(args.order.parent, relief_token, order.get("order"), record["reference_relief"], record)
    # Certificate and food-use constraints are enforced by the kit before payment.
    result["accept"] = accept_conditions
    record["selection_reason"] = pick["reason"]
    if (support_decision or {}).get("retry_of"):
        record["retry_attempt"] = True  # kept in the memory; refusal_skip leaves it out
    # Observation only: what the public stock said at selection, to compare with what the shop then answered.
    offers = shop.get("offers") or []
    record["selected_offer_qty"] = next((o.get("qty") for o in offers if o.get("sku") == selected_item), None)
    record["selected_shop_offer_units"] = sum(o.get("qty", 0) for o in offers if type(o.get("qty")) in (int, float))
    remember(args.memory, record)
    return result, record


def main():
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shops", type=Path, required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--order", type=Path)
    parser.add_argument("--budget", type=int, required=True)
    parser.add_argument("--memory", type=Path, default=ROOT / ".market/buyer-memory.jsonl")
    parser.add_argument("--memory-window", type=int, choices=(3, 5, 10, 20), default=RECENT_COUNT)
    parser.add_argument("--mode", choices=("claude", "codex"), default="claude")
    args = parser.parse_args()
    if args.budget <= 0:
        parser.error("Budget must be a positive integer")
    try:
        shops = load_shops(args.shops)
        order = json.loads(args.order.read_text(encoding="utf-8")) if args.order else {}
        acceptable = order.get("acceptable") or []
        r3 = order.get("round", 0) >= 3 or bool(acceptable)
        if not r3:
            print(json.dumps({"shop": "", "receipt": None, "stars": 3,
                              "review": "R3-only strategy: waiting for Round 3; R2 trading disabled."}), flush=True)
            return
        if args.memory == ROOT / ".market/buyer-memory.jsonl":
            args.memory = ROOT / ".market/r3-buyer-memory.jsonl"
        memory = [r for r in load_memory(args.memory) if r.get("round") == 3]
        if order.get("item") != args.item or order.get("budget") != args.budget:
            raise ValueError("Order and command arguments disagree")
        memory = reconcile(memory, payment_outcomes(args.order.parent / "payments.jsonl"))
        shops = [dict(s, offers=[o for o in s.get("offers", [])
                                if o.get("sku") in acceptable and o.get("qty", 0) > 0]) for s in shops]
        shops = [s for s in shops if s["offers"]]
        catalog_path = args.order.parent / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.exists() else {}
        snapshot = fresh(args.order.parent)
        pick = select_attempt(args.order.parent, shops, memory, catalog, args.budget, snapshot, order)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Cannot load selection inputs: {error}\n")
    reason, evidence, support_decision = pick["reason"], pick["evidence"], pick["decision"]
    print(f"[choose] {reason}", flush=True)
    first = None
    if pick["shop"]:
        result, record = trade_attempt(args, order, catalog, pick)
        # At most one retry, at another shop, for a failure seen inside this run. A refusal by the kit at payment comes after
        # this run prints its one result, and the kit then closes the order, so it is out of reach here.
        retry_reason, retry_status = should_retry(record, order, time.monotonic() - started, time.time(), retry_settings())
        if retry_reason:
            first_shop = pick["shop"]["name"]
            try:
                # The first try joins the memory, so refusal_skip and the other rules see it.
                second = select_attempt(args.order.parent, shops, memory + [record], catalog, args.budget, snapshot, order, retry_of=first_shop)
            except (OSError, ValueError) as error:
                second, retry_status = None, f"retry_selection_error_{type(error).__name__}"
            if second and second["shop"]:
                print(f"[choose] retry after {retry_reason} at {first_shop}: {second['reason']}", flush=True)
                record["buyer_support"] = support_decision
                first = {"record": record, "result": result}
                result, record = trade_attempt(args, order, catalog, second)
                reason, evidence, support_decision = second["reason"], second["evidence"], second["decision"]
            elif second:
                retry_status = "retry_" + str(second["decision"].get("status", "unknown"))
        record["retry"] = {"status": retry_status, "reason": retry_reason, **({"of": first["record"]["shop_name"]} if first else {})}
    else:
        if not shops:
            reason = "No registered shop offers an acceptable in-stock SKU."
        result = {"shop": "", "receipt": None, "stars": 3, "review": reason}
        record = {"outcome": "research_skipped", "order": order.get("order"), "selection_reason": reason,
                  "failure_reason": "selection_" + str(support_decision.get("status", "unknown"))}
    record["buyer_support"] = support_decision
    log_dir = ROOT / "logs/market-buyer"
    log_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
    (log_dir / f"{run_id}.json").write_text(
        json.dumps({"record": record, "selection_evidence": evidence, "result": result, **({"first_attempt": first} if first else {})},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
