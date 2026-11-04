"""A2A buyer with harness-driven offers and withdrawal, plus an explicit manual mode."""

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
import time
from uuid import uuid4

import httpx

from buyer_counter import evaluate as evaluate_counter
from buyer_fast import classify
from harness import HarnessError, MODELS, parse_response, select_harness
from model_router import last_call as last_model_call, run_model as run_harness
from quote_terms import check_quote

ROOT = Path(__file__).resolve().parent
TERMINAL = {"TASK_STATE_COMPLETED", "TASK_STATE_REJECTED", "TASK_STATE_CANCELED", "TASK_STATE_FAILED"}


class BuyerError(RuntimeError):
    """Transport, protocol, or budget failure; not a successful purchase."""


class BuyerTransport:
    def __init__(self, url, item, budget, *, timeout=290, transport=None, verify=True):
        if not isinstance(item, str) or not item.strip():
            raise ValueError("Item must be a nonempty name or SKU")
        if type(budget) is not int or budget <= 0:
            raise ValueError("Budget must be a positive integer")
        if not math.isfinite(timeout) or not 0 < timeout < 300:
            raise ValueError("Timeout must be positive and below 300 seconds")
        parsed = httpx.URL(url)
        if parsed.scheme not in ("http", "https") or not parsed.host:
            raise ValueError("Provide an absolute HTTP(S) seller base URL")
        self.base_url, self.item, self.budget = url.rstrip("/"), item, budget
        self.deadline = time.monotonic() + timeout
        self.endpoint = None
        self.card = None
        self.task = None
        self.receipt = None
        self.provider = None
        self.sequence = 0
        self.decisions = []
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.log_dir = ROOT / "logs/buyer" / (stamp + "-" + uuid4().hex[:8])
        self.log_dir.mkdir(parents=True)
        self.client = httpx.Client(headers={"A2A-Version": "1.0"}, trust_env=False, transport=transport,
                                   verify=verify)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.client.close()
        metadata = {"scope": "A2A transport", "item": self.item, "budget": self.budget,
                    "controller": self.provider or "manual",
                    "model": MODELS[self.provider] if self.provider else None,
                    "task_id": self.task_id, "state": self.state, "receipt": self.receipt,
                    "decisions": self.decisions,
                    "status": "error" if exc_value else "ok", "error": str(exc_value) if exc_value else None}
        (self.log_dir / "session.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    @property
    def task_id(self):
        return self.task["id"] if self.task else None

    @property
    def state(self):
        return self.task["status"]["state"] if self.task else None

    def remaining(self):
        seconds = self.deadline - time.monotonic()
        if seconds <= 0:
            raise BuyerError("Buyer time limit exceeded")
        return seconds

    def _request(self, method, url, body=None):
        self.sequence += 1
        prefix = f"{self.sequence:02d}"
        request = {"method": method, "url": str(url), "headers": {"A2A-Version": "1.0"}, "body": body}
        (self.log_dir / f"{prefix}.request.json").write_text(json.dumps(request, indent=2), encoding="utf-8")
        event = {}
        try:
            response = self.client.request(method, url, json=body, timeout=min(100, self.remaining()))
            (self.log_dir / f"{prefix}.response.txt").write_bytes(response.content)
            event["http_status"] = response.status_code
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError, BuyerError) as error:
            event["error"] = str(error)
            raise BuyerError(f"Request failed: {error}. Logs: {self.log_dir}") from error
        finally:
            (self.log_dir / f"{prefix}.http.json").write_text(json.dumps(event, indent=2), encoding="utf-8")

    def discover(self):
        card = self._request("GET", self.base_url + "/.well-known/agent-card.json")
        if not isinstance(card, dict):
            raise BuyerError("Agent Card must be a JSON object")
        interfaces = card.get("supportedInterfaces", [])
        if not isinstance(interfaces, list):
            raise BuyerError("Agent Card supportedInterfaces must be a list")
        chosen = next((entry for entry in interfaces if isinstance(entry, dict)
                       and entry.get("protocolBinding") == "JSONRPC"
                       and str(entry.get("protocolVersion")) == "1.0"), None)
        if chosen is None:
            raise BuyerError("Seller has no JSON-RPC 1.0 interface")
        endpoint = chosen.get("url")
        if not isinstance(endpoint, str):
            raise BuyerError("Agent Card interface URL is missing")
        parsed = httpx.URL(endpoint)
        if parsed.scheme not in ("http", "https") or not parsed.host:
            raise BuyerError("Agent Card must advertise an absolute HTTP(S) URL")
        self.card, self.endpoint = card, endpoint
        return card

    def _rpc(self, method, params):
        if self.endpoint is None:
            self.discover()
        request_id = str(uuid4())
        payload = self._request("POST", self.endpoint, {"jsonrpc": "2.0", "id": request_id,
                                                       "method": method, "params": params})
        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0" or payload.get("id") != request_id:
            raise BuyerError("Invalid JSON-RPC response envelope or request ID")
        if "error" in payload:
            raise BuyerError("A2A error: " + json.dumps(payload["error"]))
        result = payload.get("result")
        task = result.get("task", result) if isinstance(result, dict) else None
        if (not isinstance(task, dict) or not isinstance(task.get("id"), str) or not task["id"]
                or not isinstance(task.get("status"), dict)
                or not isinstance(task["status"].get("state"), str)):
            raise BuyerError("Expected a task with id and status")
        if self.task_id is not None and task["id"] != self.task_id:
            raise BuyerError("Seller changed taskId during the negotiation")
        if self.task and self.task.get("contextId") != task.get("contextId"):
            raise BuyerError("Seller changed contextId during the negotiation")
        self.task = task
        if self.state == "TASK_STATE_COMPLETED":
            receipts = [part["data"] for artifact in task.get("artifacts", [])
                        for part in artifact.get("parts", [])
                        if isinstance(part.get("data"), dict) and "price" in part["data"]]
            if len(receipts) != 1:
                raise BuyerError("Completed task must have one receipt with a numeric price")
            receipt = receipts[0]
            price = receipt["price"]
            if type(price) not in (int, float) or not math.isfinite(price) or not 0 < price <= self.budget:
                raise BuyerError("Receipt price is invalid or exceeds budget")
            if "item" in receipt and receipt["item"] != self.item:
                raise BuyerError("Receipt item differs from the requested item")
            self.receipt = receipt
        return task

    def open(self):
        if self.task is not None:
            raise BuyerError("This buyer already has a task")
        return self._rpc("SendMessage", {"message": {"role": "ROLE_USER", "messageId": str(uuid4()),
                         "parts": [{"text": f"I want to buy a {self.item}"}]}})

    def offer(self, price):
        if self.state != "TASK_STATE_INPUT_REQUIRED":
            raise BuyerError("Offers require an open input-required task")
        if type(price) is not int or not 0 < price <= self.budget:
            raise BuyerError("Offer must be a positive integer within budget")
        message = {"role": "ROLE_USER", "messageId": str(uuid4()), "taskId": self.task_id,
                   "parts": [{"text": str(price)}]}
        if self.task.get("contextId"):
            message["contextId"] = self.task["contextId"]
        return self._rpc("SendMessage", {"message": message})

    def get_task(self):
        if not self.task_id:
            raise BuyerError("No task to retrieve")
        return self._rpc("GetTask", {"id": self.task_id})

    def cancel(self):
        if not self.task_id or self.state in TERMINAL:
            raise BuyerError("No active task to cancel")
        return self._rpc("CancelTask", {"id": self.task_id})


def seller_message(task):
    message = task["status"].get("message") or {}
    return " ".join(part.get("text", "") for part in message.get("parts", []) if isinstance(part, dict))


def validate_buyer_decision(decision, budget):
    parse_response(json.dumps(decision))
    if decision["action"] not in ("offer", "walk_away"):
        raise ValueError("Buyer must offer or walk_away")
    if decision["action"] == "offer" and decision["price"] > budget:
        raise ValueError(f"Offer must not exceed the budget {budget}")


def record_decision(client, turn, source, reason_code, decision=None, parser_reason=None, details=None, cancel=None):
    """Leave one decision-NN.json and one trail entry on every path, including refusals and failures."""
    entry = {"turn": turn, "source": source, "reason_code": reason_code, "parser_reason": parser_reason,
             "action": decision["action"] if decision else None, "price": decision["price"] if decision else None}
    quote_check = getattr(client, "quote_check", None)
    if quote_check:
        entry["quote_check"] = quote_check
    counter_check = getattr(client, "counter_check", None)
    if counter_check:
        entry["counter_check"] = counter_check
    if cancel:
        entry["cancel"] = cancel
    route = getattr(client, "model_route", None)
    if route:
        entry.update(route)
    client.decisions.append(entry)
    (client.log_dir / f"decision-{turn:02d}.json").write_text(
        json.dumps({**(details or {}), **entry, "decision": decision}, ensure_ascii=False, indent=2),
        encoding="utf-8")


def buyer_decision(client, provider, history, turn, max_turns):
    """Ask for one decision; retry invalid JSON or budget violations once, without clamping."""
    text = seller_message(client.task)
    _, parser_reason, clear_ask = classify(text, client.budget, getattr(client, "last_offer", None))
    accept = getattr(client, "accept_conditions", None)
    client.counter_check = client.model_route = None
    client.quote_check = check_quote(client.task, text, accept) if accept else None
    if client.quote_check and client.quote_check["reason_code"]:
        reason_code = client.quote_check["reason_code"]
        skipped = {"action": "walk_away", "price": None,
                   "reason": f"Quote states terms the accept policy refuses ({reason_code}); no offer sent."}
        record_decision(client, turn, "quote_filter", reason_code, skipped, parser_reason)
        return skipped
    rule = getattr(client, "rule_decision", None)
    if rule:
        fast = rule(text)
        if fast is not None:
            validate_buyer_decision(fast, client.budget)
            record_decision(client, turn, "deterministic", parser_reason, fast, parser_reason)
            return fast
        settings = getattr(client, "over_budget_counter", None)
        if settings is not None and parser_reason == "over_budget":
            counter, client.counter_check = evaluate_counter(
                clear_ask, client.budget, getattr(client, "catalog_list_price", None),
                used=getattr(client, "counter_used", False), last_offer=getattr(client, "last_offer", None),
                config=settings)
            if counter is not None:
                validate_buyer_decision(counter, client.budget)
                client.counter_used = True
                record_decision(client, turn, "deterministic", "rule_counter_offer", counter, parser_reason)
                return counter
    if not any(float(number) > 0 for number in re.findall(r"-?\d+(?:\.\d+)?", text)):
        record_decision(client, turn, "parser", "no_positive_number", None, parser_reason)
        raise BuyerError("Seller input-required message has no valid asking price")
    gate = getattr(client, "model_gate", None)
    if gate and not gate():
        reason_code = getattr(client, "model_gate_reason", None) or "model_gate_denied"
        refused = {"action": "walk_away", "price": None,
                   "reason": f"Model negotiation unavailable ({reason_code}); no purchase forced."}
        record_decision(client, turn, "model_gate", reason_code, refused, parser_reason)
        return refused
    # Keep time for the HTTP request that follows the model decision.
    allowance = min(90, client.remaining() - 5)
    if allowance <= 0:
        record_decision(client, turn, "time_limit", "decision_time_exhausted", None, parser_reason)
        raise BuyerError("Insufficient time for another harness decision")
    deadline = time.monotonic() + allowance
    # Null when no single labeled ask exists: the last number may be a size, an hour or an offer count.
    context = {"item": client.item, "budget": client.budget, "current_ask": clear_ask,
               "seller_message": text, "history": history,
               "remaining_turns": max_turns - turn + 1, "remaining_seconds": round(client.remaining(), 2)}
    context["r3_policy_evidence"] = getattr(client, "r3_context", None)
    instructions = getattr(client, "strategy_prompt", ROOT / "prompts/buyer.txt").read_text(encoding="utf-8")
    prompt = instructions + "\nInput data:\n" + json.dumps(context, ensure_ascii=False)
    record = {"task_id": client.task_id, "provider": provider, "model": MODELS[provider],
              "context": context, "status": "failed", "attempts": []}
    decision = None
    try:
        for attempt in range(2):
            entry = {"attempt": attempt + 1, "prompt": prompt}
            record["attempts"].append(entry)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BuyerError("Buyer decision time limit exceeded")
            try:
                decision = run_harness(provider, prompt, timeout=remaining)
                entry["response"] = decision
                route = last_model_call()
                if route:
                    client.model_route = route
                    entry.update(route)
                    record.update(route)
                validate_buyer_decision(decision, client.budget)
            except (ValueError, HarnessError) as error:
                entry["error"] = str(error)
                validation_error = isinstance(error, ValueError) or isinstance(error.__cause__, ValueError)
                if not validation_error or attempt == 1:
                    if isinstance(error, HarnessError):
                        # The model is unavailable: end this order cleanly. A bad budget answer stays an error below.
                        return {"action": "walk_away", "price": None, "reason": "Model unavailable; no purchase forced."}
                    raise BuyerError(f"Buyer decision failed: {error}") from error
                prompt += ("\nYour previous response was invalid. Error: " + str(error)
                           + "\nReturn a corrected JSON object obeying the original rules.")
                continue
            record["status"] = "ok"
            return decision
        raise BuyerError("No valid buyer decision")
    finally:
        settled = decision if record["status"] == "ok" else None
        record_decision(client, turn, "model", f"model_{settled['action']}" if settled else "model_failed",
                        settled, parser_reason, record)


def cancel_after_turn_limit(client, turn):
    """Close a task left open by the turn limit. A failed cancel is recorded and never replaces the result."""
    client.quote_check = client.counter_check = None
    outcome = {"attempted": False, "result": "skipped_has_receipt", "state": client.state, "error": None}
    if not (client.receipt or getattr(client, "raw_receipt", None)):
        outcome["attempted"] = True
        try:
            client.cancel()
            outcome["result"] = "canceled" if client.state == "TASK_STATE_CANCELED" else "not_confirmed"
        except Exception as error:
            outcome.update(result="failed", error=str(error)[:200] or type(error).__name__)
        outcome["state"] = client.state
    record_decision(client, turn, "turn_limit", "turn_limit_exceeded", cancel=outcome)


def run_autonomous(client, provider, *, max_turns=12):
    client.provider = provider
    client.open()
    history = [{"role": "seller", "text": seller_message(client.task), "state": client.state}]
    turn = 0
    outcome = None
    while client.state not in TERMINAL:
        if client.state != "TASK_STATE_INPUT_REQUIRED":
            raise BuyerError("Expected an input-required or terminal task from the seller")
        turn += 1
        if turn > max_turns:
            cancel_after_turn_limit(client, turn)
            raise BuyerError("Buyer decision turn limit exceeded")
        decision = buyer_decision(client, provider, history, turn, max_turns)
        history.append({"role": "buyer", "decision": decision})
        if decision["action"] == "walk_away":
            client.cancel()
            if client.state != "TASK_STATE_CANCELED":
                raise BuyerError("Seller did not confirm cancellation")
            outcome = "walked_away"
            break
        client.offer(decision["price"])
        history.append({"role": "seller", "text": seller_message(client.task), "state": client.state})
    if client.state == "TASK_STATE_FAILED":
        raise BuyerError("Seller task failed")
    if outcome is None:
        outcome = {"TASK_STATE_COMPLETED": "purchased", "TASK_STATE_REJECTED": "seller_rejected",
                   "TASK_STATE_CANCELED": "canceled"}[client.state]
    summary = {"outcome": outcome, "harness": provider, "model": MODELS[provider],
               "item": client.item, "budget": client.budget, "task_id": client.task_id,
               "state": client.state, "receipt": client.receipt, "turns": turn,
               "decisions": client.decisions}
    (client.log_dir / "outcome.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", type=int, required=True)
    parser.add_argument("--manual", action="store_true",
                        help="Exercise transport using explicit offers, without a model")
    parser.add_argument("--mode", choices=["claude", "codex", "mixed"])
    parser.add_argument("--buyer-harness", choices=list(MODELS), default="codex")
    parser.add_argument("--offer", action="append", type=int, default=[])
    parser.add_argument("--cancel", action="store_true", help="Cancel if still active after the supplied offers")
    args = parser.parse_args()
    if args.manual and args.mode:
        parser.error("Choose either --manual or --mode")
    if not args.manual and (not args.mode or args.offer or args.cancel):
        parser.error("Autonomous mode requires --mode and cannot use --offer or --cancel")
    buyer = None
    try:
        with BuyerTransport(args.url, args.item, args.budget) as buyer:
            if not args.manual:
                provider = select_harness(args.mode, "buyer", buyer=args.buyer_harness)
                print(json.dumps(run_autonomous(buyer, provider), ensure_ascii=False), flush=True)
                return
            task = buyer.open()
            print(json.dumps(task, ensure_ascii=False), flush=True)
            for offer in args.offer:
                if buyer.state in TERMINAL:
                    break
                print(json.dumps(buyer.offer(offer), ensure_ascii=False), flush=True)
            if args.cancel and buyer.state not in TERMINAL:
                print(json.dumps(buyer.cancel(), ensure_ascii=False), flush=True)
    except (BuyerError, ValueError) as error:
        parser.exit(1, str(error) + "\n")
    finally:
        if buyer is not None:
            print(f"Buyer logs: {buyer.log_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
