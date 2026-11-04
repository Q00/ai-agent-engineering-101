"""R3 buyer: sparse seller-profit research with verified settlement feedback."""

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
from buyer_memory import load_memory, remember, RECENT_COUNT
from shop_catalog import load_shops
from r3_policy import buyer_accept, load_policy
from settlement_memory import payment_outcomes, reconcile
from market_intel import fresh
from buyer_support import prepare as prepare_support

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
    requirements = None

    def open(self):
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
                              task=self.task_id, agreed_price=self.last_offer)


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


def trade(shop, item, budget, *, mode="claude", transport=None, requirements=None, decision_context=None, max_turns=6, fast_rules=False, model_gate=None, accept_conditions=None):
    client = MarketTransport(shop["url"], item, budget, transport=transport, timeout=180)
    client.requirements = requirements
    client.accept_conditions = accept_conditions
    client.strategy_prompt = ROOT / "prompts/buyer-r3.txt"
    client.r3_context = decision_context
    if fast_rules:
        from buyer_fast import decision
        client.rule_decision = lambda text: decision(text, client.budget, client.last_offer)
        cached = []
        def allow_model():
            if not cached:
                answer = model_gate() if model_gate else "model_gate_missing"
                client.model_gate_reason = {True: "reserved", False: "model_gate_denied"}.get(answer, answer)
                cached.append(client.model_gate_reason == "reserved")
            return cached[0]
        client.model_gate = allow_model
    error = negotiation = None
    try:
        with client:
            summary = run_autonomous(client, mode, max_turns=max_turns)
        negotiation = summary["outcome"]
        outcome = "purchased" if negotiation == "purchased" else "no_deal"
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
              "negotiation_outcome": negotiation, "decision_trail": client.decisions,
              "failure_reason": failure_reason(outcome, negotiation, client.decisions),
              "buyer_log_dir": str(client.log_dir)}
    result = {"shop": shop["url"], "receipt": receipt if outcome == "purchased" else None,
              "stars": stars, "review": review}
    return result, record


def main():
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
        accept_conditions = None
        if r3:
            if order.get("item") != args.item or order.get("budget") != args.budget:
                raise ValueError("Order and command arguments disagree")
            accept_conditions = buyer_accept(order)
            memory = reconcile(memory, payment_outcomes(args.order.parent / "payments.jsonl"))
            shops = [dict(s, offers=[o for o in s.get("offers", [])
                                    if o.get("sku") in acceptable and o.get("qty", 0) > 0]) for s in shops]
            shops = [s for s in shops if s["offers"]]
            catalog_path = args.order.parent / "catalog.json"
            catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.exists() else {}
            snapshot = fresh(args.order.parent)
            shop, selected_item, trade_budget, reason, evidence, support_decision = prepare_support(
                args.order.parent, shops, memory, catalog, args.budget, load_policy()["buying"],
                None, snapshot, order)
            selected_evidence = next((e for e in evidence if shop and e["shop"] == shop["name"] and e["sku"] == selected_item), {})
            if selected_evidence.get("warranty_required"):
                accept_conditions["warranty_hours"] = selected_evidence["warranty_required"]
    except (OSError, ValueError) as error:
        parser.exit(1, f"Cannot load selection inputs: {error}\n")
    print(f"[choose] {reason}", flush=True)
    if shop:
        trade_options = {"mode": args.mode, "decision_context": selected_evidence,
                         "max_turns": support_decision["max_turns"], "accept_conditions": accept_conditions}
        if support_decision.get("fast_rules"):
            from buyer_support import model_reservation
            trade_options.update(fast_rules=True, model_gate=lambda: model_reservation(args.order.parent, order["order"]))
        if r3 and order.get("kind") == "goods":
            trade_options["requirements"] = accept_conditions
        result, record = trade(shop, selected_item, trade_budget, **trade_options)
        if r3:
            record["round"] = 3
            record["order"] = order.get("order")
            record["order_budget"] = args.budget
            record["category"] = order.get("category", args.item)
            record["accept_conditions"] = accept_conditions
            # Certificate and food-use constraints are enforced by the kit before payment.
            result["accept"] = accept_conditions
        record["selection_reason"] = reason
        remember(args.memory, record)
    else:
        if r3 and not shops:
            reason = "No registered shop offers an acceptable in-stock SKU."
        result = {"shop": "", "receipt": None, "stars": 3, "review": reason}
        record = {"outcome": "research_skipped", "order": order.get("order"), "selection_reason": reason,
                  "failure_reason": "selection_" + str(support_decision.get("status", "unknown"))}
    record["buyer_support"] = support_decision
    log_dir = ROOT / "logs/market-buyer"
    log_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
    (log_dir / f"{run_id}.json").write_text(
        json.dumps({"record": record, "selection_evidence": evidence, "result": result},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
