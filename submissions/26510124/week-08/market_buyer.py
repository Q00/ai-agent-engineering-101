"""Market buyer with name-based memory and the week-06 negotiation loop.

    python market_buyer.py --shops .market/shops.json --item lamp --budget 120
The configured four decisions and 270-second deadline are shared across at most
two shops. A second shop requires a confirmed end without any observed receipt.
"""
import argparse
import json
import math
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from harness import HarnessError, buyer_decision, decide, parse_object
from inventory import read_json
from payment_memory import recent_jsonl, reconcile, attach_reviews
from buyer_policy import buying_terms, checkout_policy, load_policy, negotiation_policy
from buyer_selection import shortlist, offer_fingerprint, reuse_failures, compatibility_fingerprint
from buyer_context import compact_dialogue
from buyer_fast_selection import recent_success_choice, timeout_choice, value_choice
from buyer_value_memory import build_value_evidence
from buyer_public_prices import read_public_prices, read_food_discovery, merge_price_evidence
from buyer_retry import remaining_retry_decisions, select_alternative
from buyer_quote_accept import certified_choice
from buyer_food_horizon import filter_recent_short_life, prioritize_history


VERSION_HEADERS = {"A2A-Version": "1.0"}
TERMINAL_STATES = {
    "TASK_STATE_COMPLETED", "TASK_STATE_REJECTED", "TASK_STATE_CANCELED",
    "TASK_STATE_FAILED",
}
MAX_DECISIONS = 12
RUN_SECONDS = 270
ROOT = Path(__file__).resolve().parent


class BuyerError(RuntimeError):
    """A protocol, safety, or execution failure; no fallback purchase is made."""


class ReceiptMismatch(BuyerError):
    """The completed receipt does not match the authorized purchase."""


class PolicyRefusal(BuyerError):
    """A model choice violates the owner's purchasing limits; no fallback bid."""

    def __init__(self, message, *, reason="unknown"):
        super().__init__(message)
        self.reason = reason


def read_shops(path):
    shops = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(shops, list) or not shops:
        raise BuyerError("Shop list must be a nonempty JSON array")
    names = set()
    for shop in shops:
        if not isinstance(shop, dict):
            raise BuyerError("Each shop must be an object")
        name, url = shop.get("name"), shop.get("url")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise BuyerError("Each shop must have a distinct nonempty name")
        if not isinstance(url, str) or urlsplit(url).scheme not in {"http", "https"} or not urlsplit(url).netloc:
            raise BuyerError("Shop URL must be an HTTP(S) card base URL")
        if not isinstance(shop.get("reviews", []), list):
            raise BuyerError("Shop reviews must be an array")
        names.add(name)
    return shops


def read_memory(path, payments=None, reviews=None):
    records = recent_jsonl(path)
    if any(not isinstance(r.get("name"), str) for r in records):
        raise BuyerError("Invalid shop name in memory")
    if payments:
        records = reconcile(records, recent_jsonl(payments))
    return attach_reviews(records, recent_jsonl(reviews)) if reviews else records


def stocked_shops(shops, order):
    """Keep only offered SKUs allowed by THIS order; never infer stock from prose."""
    acceptable = set(order.get("acceptable") or [])
    if not acceptable:
        raise BuyerError("Round 3 order has no acceptable SKUs")
    result = []
    for shop in shops:
        offers = [o for o in shop.get("offers", []) if isinstance(o, dict)
                  and o.get("sku") in acceptable and type(o.get("qty")) is int and o["qty"] > 0]
        if offers:
            result.append({**shop, "offers": offers})
    if not result:
        raise BuyerError("No shop offers an acceptable SKU in stock")
    return result


def acceptance(order):
    return checkout_policy(order)


def choose_shop(shops, memory, *, item, budget, deadline, policy=None, order=None, catalog=None,
                market_dir=None, selection_state=None):
    order = order or {}
    policy = policy or load_policy()
    seed = order.get("order") or f"{item}:{int(time.time() // 900)}"
    shops, life_skipped = filter_recent_short_life(shops, memory, order, policy)
    shops, cooldown = reuse_failures(shops, memory, item=item, budget=budget,
                                     config=policy, order=order, catalog=catalog)
    values = build_value_evidence(memory, catalog=catalog, order=order, budget=budget, config=policy)
    food_discovery = {}
    if market_dir is not None:
        public = read_public_prices(market_dir, shops=shops, catalog=catalog,
                                    order=order, budget=budget, config=policy)
        values = merge_price_evidence(values, public)
        if order.get("kind") == "food":
            food_discovery = read_food_discovery(market_dir, shops=shops, catalog=catalog,
                                                 order=order, budget=budget, config=policy)
    # Discovery lacks expiry evidence: selection only, never negotiation references.
    selection_values = merge_price_evidence(values, food_discovery)
    selection_values = prioritize_history(selection_values, order, policy)
    candidates = shortlist(shops, memory, seed=seed,
                           limit=policy.get("selection_max_candidates", 6),
                           round_number=order.get("round"), value_evidence=selection_values)
    if not candidates:
        raise BuyerError("No eligible candidate shops after current-stock and recent-refusal checks")
    names = {s["name"] for s in candidates}

    def validate(output):
        decision = parse_object(output)
        if not isinstance(decision.get("name"), str) or decision["name"] not in names:
            raise HarnessError("Choose one exact name from the supplied candidates")
        selected = next(s for s in candidates if s["name"] == decision["name"])
        if selected["offers"] and decision.get("sku") not in {o["sku"] for o in selected["offers"]}:
            raise HarnessError("Choose a SKU from this shop's offered items")
        return decision

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BuyerError("Shop selection deadline exceeded")
    timeout = policy.get("selection_timeout_seconds", 15)
    if type(timeout) is not int or not 1 <= timeout <= 45:
        raise BuyerError("Invalid buyer policy: selection_timeout_seconds")
    context = {"item": item, "budget": budget, "candidates": candidates, "purchase_policy": negotiation_policy(policy)}
    if type(order.get("round")) is int and order["round"] >= 3:
        context["order_conditions"] = {"kind": order.get("kind"), "accept": checkout_policy(order, policy)}
    if isinstance(catalog, dict):
        # Shared prices avoid repeating catalog values for the same SKU in six shops.
        context["catalog_prices"] = {
            offer["sku"]: catalog[offer["sku"]]["list_price"]
            for candidate in candidates for offer in candidate["offers"]
            if isinstance(catalog.get(offer["sku"]), dict)
            and type(catalog[offer["sku"]].get("list_price")) is int
            and catalog[offer["sku"]]["list_price"] > 0}
    log("SELECTION CONTEXT", {"eligible_shops": len(shops), "candidates": len(candidates),
                              "context_chars": len(json.dumps(context, ensure_ascii=False)),
                              **cooldown,
                              "certified_short_life_skipped_offers": life_skipped,
                              "food_discovery_pairs": len(food_discovery),
                              "stocked_offers_scanned": sum(len(s.get("offers", [])) for s in shops),
                              "value_candidates": [c["name"] for c in candidates if c["selection_role"] == "value"],
                              "public_price_candidates": sum(any(
                                  g.get("source") == "public_ledger" for o in c["offers"]
                                  for g in o.get("price_history", [])) for c in candidates),
                              "exploration": [c["name"] for c in candidates if c["selection_role"] == "exploration"]})
    choice = value_choice(candidates, values=selection_values, budget=budget, config=policy,
                          order=order, catalog=catalog, seed=seed)
    method = "repeated_value" if choice is not None else "model"
    if choice is not None and order.get("kind") == "food" and food_discovery:
        method = "food_bargain_scan"
    if choice is None:
        choice = recent_success_choice(candidates, memory, budget=budget, config=policy,
                                       order=order, catalog=catalog, seed=seed)
        if choice is not None:
            method = "recent_settlement"
    if choice is None:
        try:
            choice = decide("market_select.txt", context, validate, timeout=min(timeout, remaining),
                            prefix="market-select", max_attempts=1)
        except HarnessError as exc:
            # These prefixes are emitted by our harness, never by seller text.
            # Invalid JSON, invented shops/SKUs and CLI/auth failures still fail.
            if not str(exc).startswith(("Harness timed out;", "Harness deadline expired;")):
                raise
            if time.monotonic() >= deadline:
                raise BuyerError("Shop selection deadline exceeded") from exc
            choice = timeout_choice(candidates, seed=seed)
            if choice is None:
                raise BuyerError("No eligible candidate remains for selection") from exc
            method = "timeout_local_comparison"
            log("SELECTION TIMEOUT", str(exc))
    choice = validate(json.dumps(choice, ensure_ascii=False))
    log("SELECTION METHOD", {"method": method, "model_timeout_seconds": min(timeout, remaining)})
    log("SHOP SELECTION", choice)
    shop = next(s for s in shops if s["name"] == choice["name"])
    reference = values.get((shop["name"], choice.get("sku")), {}).get("groups")
    if selection_state is not None:
        # Local data only: a registry's arbitrary extra fields cannot seed retries.
        selection_state.update(shops=shops, candidates=candidates, values=values, seed=seed)
    return {**shop, "selected_sku": choice.get("sku"),
            "price_reference": reference or []}, choice["reason"]


def log(label, value):
    """The trade test captures stdout; keep requests and responses unchanged."""
    print(f"{label}: {json.dumps(value, ensure_ascii=False)}", flush=True)


def message_text(task):
    message = task.get("status", {}).get("message") or {}
    return " ".join(part["text"] for part in message.get("parts", [])
                    if isinstance(part, dict) and isinstance(part.get("text"), str))


def last_asking_price(text):
    """Read a labelled quote, never a SKU, warranty duration or expiry timestamp."""
    matches = re.findall(r"(?:asking(?: price)?|counteroffer|price|quote)\s*(?:is\s*)?[:=]?\s*"
                         r"[₩$]?\s*([0-9][0-9,]*(?:\.[0-9]+)?)", text, re.IGNORECASE)
    values = {float(value.replace(",", "")) for value in matches}
    if len(values) == 1:
        return values.pop()
    if not matches and re.fullmatch(r"\s*[0-9]+\s*", text):
        return float(text)
    return None


class Buyer:
    def __init__(self, client, *, url, item, budget, seconds=RUN_SECONDS, policy=None,
                 order=None, market_dir=None, shop_name=None):
        self.client = client
        self.base_url = url.rstrip("/")
        self.endpoint = None
        self.item = item
        self.budget = budget
        self.policy = policy or buying_terms(budget)
        self.order, self.market_dir, self.shop_name = order or {}, market_dir, shop_name
        self.fast_accepts, self.model_decisions = 0, 0
        self.fast_accept_skip_reasons = {}
        self.fast_accept_missing_fields = {}
        self.food_lifetime_observation = None
        self.price_limit = min(budget, self.policy["price_limit"])
        self.no_deal_reason = ""
        self.started_at = time.monotonic()
        self.deadline = self.started_at + seconds
        self.reason_code = "unknown"
        self.stage = "discovery"
        self.decisions = 0
        self.last_parsed_ask = None
        self.seller_terminal = None
        self.task_id = None
        self.terminal = False
        self.terminal_state = None
        self.cancel_confirmed = False
        self.receipt_seen = False
        self.history = []
        self.authorized_price = None
        self.receipt = None
        self.observed_receipt = None

    def diagnostics(self):
        """Local observations only; seller claims never establish a root cause."""
        return {"version": 1, "reason": self.reason_code, "stage": self.stage,
                "fast_accepts": self.fast_accepts, "model_decisions": self.model_decisions,
                "fast_accept_skip_reasons": dict(self.fast_accept_skip_reasons),
                "fast_accept_missing_fields": dict(self.fast_accept_missing_fields),
                "decisions": self.decisions, "last_parsed_ask": self.last_parsed_ask,
                "last_authorized_bid": self.authorized_price,
                "elapsed_seconds": round(max(0, time.monotonic() - self.started_at), 3),
                "task_id": self.task_id, "seller_terminal": self.seller_terminal,
                "terminal_state": self.terminal_state, "cancel_confirmed": self.cancel_confirmed,
                "receipt_seen": self.receipt_seen}

    def observe_task(self, task):
        """Any artifact/data after an offer makes retry unsafe, even if malformed."""
        state = task.get("status", {}).get("state")
        self.terminal_state = state if state in TERMINAL_STATES else None
        self.terminal = state in TERMINAL_STATES
        def uncertain_message(message):
            if not isinstance(message, dict):
                return True
            parts = message.get("parts", [])
            return (not isinstance(parts, list) or len(parts) > 32
                    or any(not isinstance(p, dict) or "data" in p for p in parts))

        message = task.get("status", {}).get("message") or {}
        history = task.get("history")
        history_unknown = history is not None and (not isinstance(history, list)
            or len(history) > 32 or any(uncertain_message(m) for m in history))
        artifacts = task.get("artifacts")
        if (state == "TASK_STATE_COMPLETED" or artifacts
                or artifacts is not None and not isinstance(artifacts, list)
                or uncertain_message(message) or history_unknown):
            self.receipt_seen = True

    def remaining(self):
        seconds = self.deadline - time.monotonic()
        if seconds <= 0:
            raise BuyerError("Buyer deadline exceeded")
        return seconds

    def request(self, method, url, *, body=None, cleanup=False):
        timeout = 3 if cleanup else min(90, self.remaining())
        log("HTTP REQUEST", {"method": method, "url": url,
                             "headers": VERSION_HEADERS, "body": body})
        try:
            response = self.client.request(method, url, headers=VERSION_HEADERS,
                                           json=body, timeout=timeout)
            log("HTTP RESPONSE", {"status": response.status_code, "body": response.text})
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BuyerError(f"{method} {url} failed: {exc}") from exc
        if not isinstance(data, dict):
            raise BuyerError("Expected a JSON object from the server")
        return data

    def discover(self):
        card = self.request("GET", self.base_url + "/.well-known/agent-card.json")
        for interface in card.get("supportedInterfaces", []):
            if not isinstance(interface, dict):
                continue
            url = interface.get("url")
            if (interface.get("protocolBinding") == "JSONRPC"
                    and interface.get("protocolVersion") == "1.0"
                    and isinstance(url, str) and url.startswith(("http://", "https://"))):
                self.endpoint = url
                return
        raise BuyerError("Agent Card has no A2A 1.0 JSONRPC interface")

    def rpc(self, method, params, *, cleanup=False):
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4()),
                "method": method, "params": params}
        reply = self.request("POST", self.endpoint, body=body, cleanup=cleanup)
        if reply.get("jsonrpc") != "2.0" or reply.get("id") != body["id"]:
            raise BuyerError("Invalid JSON-RPC version or response id")
        if "error" in reply:
            raise BuyerError(f"{method}: {json.dumps(reply['error'], ensure_ascii=False)}")
        result = reply.get("result")
        if not isinstance(result, dict):
            raise BuyerError("JSON-RPC response has no result object")
        task = result.get("task", result)
        if not isinstance(task, dict) or not isinstance(task.get("status"), dict):
            raise BuyerError("Seller must respond with an A2A Task")
        task_id = task.get("id")
        if not isinstance(task_id, str) or not task_id:
            raise BuyerError("Seller Task has no id")
        if self.task_id is not None and self.task_id != task_id:
            raise BuyerError("Seller changed taskId during the negotiation")
        self.task_id = task_id
        self.observe_task(task)
        return task

    def send(self, text):
        message = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()),
                   "parts": [{"text": text}]}
        if self.task_id is not None:
            message["taskId"] = self.task_id
        self.history.append({"role": "buyer", "text": text})
        return self.rpc("SendMessage", {"message": message})

    def cancel(self, *, cleanup=False):
        task = self.rpc("CancelTask", {"id": self.task_id}, cleanup=cleanup)
        self.observe_task(task)
        if task.get("id") != self.task_id or task["status"].get("state") != "TASK_STATE_CANCELED":
            raise BuyerError("Seller did not confirm cancellation")
        self.cancel_confirmed = True

    def validate_decision(self, decision, seller_message, *, certified_ask=None):
        if not isinstance(decision, dict):
            raise BuyerError("Harness decision must be an object")
        action = decision.get("action")
        if action not in {"offer", "accept", "walk_away"}:
            raise BuyerError("Harness returned an unknown buyer action")
        if not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
            raise BuyerError("Harness decision must include its reason")
        if action == "walk_away":
            return
        price = decision.get("price")
        if type(price) is not int or not 0 < price <= self.budget:
            raise BuyerError("Harness price must be a positive integer within the budget")
        if price > self.price_limit:
            raise PolicyRefusal("Model price exceeds the owner's purchasing limit", reason="price_limit")
        exact_ask = certified_ask if certified_ask is not None else last_asking_price(seller_message)
        accepts_current_ask = exact_ask is not None and price == exact_ask
        if (self.authorized_price is not None
                and price > self.authorized_price + self.policy["max_concession"]
                and not ((action == "accept" or accepts_current_ask)
                         and self.policy.get("allow_accept_above_concession") is True)):
            raise PolicyRefusal("Model concession exceeds the per-turn limit", reason="concession_limit")
        if action == "accept" and price != exact_ask:
            raise BuyerError("Harness acceptance price does not match the seller's ask")

    def verify_receipt(self, task):
        receipts = []
        for artifact in task.get("artifacts", []):
            for part in artifact.get("parts", []):
                data = part.get("data")
                if isinstance(data, dict) and "price" in data:
                    receipts.append(data)
        if len(receipts) != 1:
            raise ReceiptMismatch("Completed task must contain exactly one receipt data Part")
        self.observed_receipt = receipts[0]
        price = self.observed_receipt["price"]
        if (type(price) not in (int, float) or not math.isfinite(price)
                or not float(price).is_integer() or not 0 < price <= self.budget
                or price > self.price_limit
                or price != self.authorized_price):
            raise ReceiptMismatch(
                f"Authorized {self.authorized_price}, receipt price {price!r}, budget {self.budget}")
        if self.observed_receipt.get("item") != self.item:
            raise ReceiptMismatch("Receipt item differs from the ordered item")
        if "task" in self.observed_receipt and self.observed_receipt["task"] != self.task_id:
            raise ReceiptMismatch("Receipt task differs from the negotiation taskId")
        # Keep the exact data Part, including sig_seller and unknown extra fields.
        self.receipt = self.observed_receipt
        log("RECEIPT RECEIVED — NOT PAYMENT", {"item": self.item, "price": price,
                         "budget": self.budget, "taskId": self.task_id})

    def run(self):
        try:
            return self.negotiate()
        except PolicyRefusal as exc:
            self.reason_code = exc.reason
            self.no_deal_reason = str(exc)
            if self.task_id is not None and not self.terminal:
                try:
                    self.cancel(cleanup=True)
                except BuyerError as cleanup:
                    log("CLEANUP ERROR", str(cleanup))
            log("POLICY WALK AWAY", self.no_deal_reason)
            return "no_deal"
        except (BuyerError, HarnessError) as failure:
            if isinstance(failure, ReceiptMismatch):
                self.reason_code = "receipt_mismatch"
            elif isinstance(failure, HarnessError):
                self.reason_code = ("model_timeout" if str(failure).startswith(
                    ("Harness timed out;", "Harness deadline expired;")) else "model_error")
            else:
                self.reason_code = ("model_error" if self.stage == "decision_validation"
                                    else "protocol_or_network_error")
            # Ending on an error is not a model's walk-away decision. Report the
            # failure and release a pending Task, but never choose another price.
            if self.task_id is not None and not self.terminal:
                try:
                    self.cancel(cleanup=True)
                except BuyerError as exc:
                    log("CLEANUP ERROR", str(exc))
            raise

    def negotiate(self):
        self.discover()
        self.stage = "opening_request"
        task = self.send(f"I want to buy a {self.item}")
        decisions = 0
        while True:
            self.remaining()
            self.observe_task(task)
            state = task["status"].get("state")
            text = message_text(task)
            ask = last_asking_price(text)
            if ask is not None and math.isfinite(ask):
                self.last_parsed_ask = ask
            if state in TERMINAL_STATES:
                self.seller_terminal = {
                    "state": state, "source": "untrusted_seller",
                    "reported_tags": [tag for tag, pattern in (
                        ("stock", r"stock|inventory|재고|품절"),
                        ("price", r"price|expensive|cheap|가격|비싸|저렴"))
                        if re.search(pattern, text, re.IGNORECASE)]}
            if text and state == "TASK_STATE_INPUT_REQUIRED":
                self.history.append({"role": "seller", "text": text})
            if state == "TASK_STATE_COMPLETED":
                self.stage = "receipt_validation"
                self.verify_receipt(task)
                self.reason_code = "receipt_issued"
                return "receipt_issued"
            if state in {"TASK_STATE_REJECTED", "TASK_STATE_CANCELED"}:
                self.stage = "seller_terminal"
                self.reason_code = "seller_rejected" if state == "TASK_STATE_REJECTED" else "seller_cancelled"
                log("NO DEAL", {"state": state, "taskId": self.task_id})
                return "no_deal"
            if state in {"TASK_STATE_SUBMITTED", "TASK_STATE_WORKING"}:
                self.stage = "seller_poll"
                time.sleep(min(0.25, self.remaining()))
                task = self.rpc("GetTask", {"id": self.task_id})
                continue
            if state != "TASK_STATE_INPUT_REQUIRED":
                raise BuyerError(f"Cannot negotiate in seller state {state}: {text}")
            if decisions >= min(MAX_DECISIONS, self.policy["max_decisions"]):
                self.stage = "decision_limit"
                raise PolicyRefusal("Negotiation ended at the owner's turn limit without a satisfactory agreement",
                                    reason="decision_limit")
            compact = compact_dialogue(self.history, text, last_asking_price)
            log("NEGOTIATION CONTEXT", {"decision": decisions + 1,
                                        "context_chars": len(json.dumps(compact, ensure_ascii=False))})
            self.stage = "model_decision"
            self.decisions = decisions + 1
            observation = {}
            decision = certified_choice(task, item=self.item, shop_name=self.shop_name,
                                        order=self.order, policy=self.policy, market_dir=self.market_dir,
                                        seconds_left=self.remaining(), observation=observation)
            # A later unknown quote clears earlier proof: never infer unchanged units.
            self.food_lifetime_observation = observation.get("food_lifetime")
            certified_ask = decision["price"] if decision is not None else None
            if decision is not None:
                self.fast_accepts += 1
                log("BUYER DECISION SOURCE", "certified_target_quote")
            else:
                skip_reason = observation.get("reason", "unknown")
                self.fast_accept_skip_reasons[skip_reason] = self.fast_accept_skip_reasons.get(skip_reason, 0) + 1
                missing = observation.get("missing_fields", [])
                for field in missing:
                    self.fast_accept_missing_fields[field] = self.fast_accept_missing_fields.get(field, 0) + 1
                log("FAST ACCEPT SKIPPED", {"reason": skip_reason, "missing_fields": missing})
                self.model_decisions += 1
                decision = buyer_decision(item=self.item, budget=self.budget,
                                      **compact,
                                      timeout=min(90, self.remaining()), policy=self.policy,
                                      current_ask=last_asking_price(text),
                                      decisions_left=min(MAX_DECISIONS, self.policy["max_decisions"]) - decisions)
            decisions += 1
            log("BUYER DECISION", decision)
            self.stage = "decision_validation"
            self.validate_decision(decision, text, certified_ask=certified_ask)
            self.remaining()
            if decision["action"] == "walk_away":
                self.stage = "buyer_cancel"
                self.reason_code = "model_walk_away"
                self.no_deal_reason = decision["reason"]
                self.cancel()
                log("NO DEAL", {"reason": decision["reason"], "taskId": self.task_id})
                return "no_deal"
            # The wire contract uses a numeric offer for both acceptance and
            # counteroffers. The model or certified target shortcut chooses the price.
            self.authorized_price = decision["price"]
            self.stage = "offer_request"
            task = self.send(str(self.authorized_price))


def trade_and_record(shop, *, item, budget, memory_path, reason, seconds, client, order=None, policy=None,
                     attempt_status=None, market_dir=None):
    policy = policy or buying_terms(budget, order=order)
    buyer = Buyer(client, url=shop["url"], item=item, budget=budget, seconds=seconds, policy=policy,
                  order=order, market_dir=market_dir, shop_name=shop["name"])
    detail = ""
    try:
        outcome = buyer.run()
    except ReceiptMismatch as exc:
        outcome, detail = "receipt_mismatch", str(exc)
    except (BuyerError, HarnessError) as exc:
        outcome, detail = "error", str(exc)
    if outcome == "receipt_issued":
        stars, review = 3, f"Agreed {item} at {buyer.receipt['price']}; receipt matched. Payment checked by kit afterwards."
    elif outcome == "no_deal":
        detail = buyer.no_deal_reason
        stars, review = 3, f"No deal for {item}; no completed purchase."
    elif outcome == "receipt_mismatch":
        stars, review = 1, f"Receipt mismatch: {detail}"
    else:
        stars, review = 2, f"Trade could not be completed: {detail}"
    record = {
        "time": datetime.now(timezone.utc).isoformat(),
        "name": shop["name"], "url": shop["url"], "item": item, "budget": budget,
        "price": buyer.receipt["price"] if buyer.receipt else None,
        "result": outcome, "detail": detail, "selection_reason": reason,
        "authorized_price": buyer.authorized_price,
        "order": (order or {}).get("order"), "round": (order or {}).get("round", 2),
        "purchase_policy": policy,
        "selection_fingerprint": offer_fingerprint(shop, item, policy, (order or {}).get("round", 2)),
        "compatibility_fingerprint": compatibility_fingerprint(shop, item, policy, (order or {}).get("round", 2)),
        "observed_receipt": buyer.observed_receipt,
        "food_lifetime_observation": buyer.food_lifetime_observation,
        "observed_stock_qty": next((o.get("qty") for o in shop.get("offers", []) if o.get("sku") == item), None),
        "negotiation_diagnostics": buyer.diagnostics(),
        "stars": stars, "review": review,
    }
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    with memory_path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
    if attempt_status is not None:
        attempt_status.update(buyer.diagnostics(), outcome=outcome,
                              no_deal_only=(outcome == "no_deal" and buyer.receipt is None
                                            and buyer.observed_receipt is None))
    return {"shop": shop["url"], "receipt": buyer.receipt, "stars": stars, "review": review,
            **({"accept": policy["accept"]} if order and order.get("round", 0) >= 3 else {})}


def alternative_shop(selection, primary, order):
    """Choose once from the original filtered shortlist; never from seller prose."""
    primary_url = primary["url"].rstrip("/")
    shops = {s["name"]: s for s in selection.get("shops", [])
             if s["name"] != primary["name"] and s["url"].rstrip("/") != primary_url}
    candidates = [c for c in selection.get("candidates", []) if c["name"] in shops]
    choice = select_alternative(candidates, primary, seed=selection.get("seed", ""))
    if choice is None:
        return None
    shop, sku = shops[choice["name"]], choice.get("sku")
    if (sku not in order.get("acceptable", []) or not any(
            o.get("sku") == sku and type(o.get("qty")) is int and o["qty"] > 0
            for o in shop.get("offers", []))):
        return None
    reference = selection.get("values", {}).get((shop["name"], sku), {}).get("groups", [])
    return {**shop, "selected_sku": sku, "price_reference": reference}, choice["reason"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shops", type=Path, required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", type=int, required=True)
    parser.add_argument("--order", type=Path)
    parser.add_argument("--memory", type=Path, default=ROOT / "data" / "market-memory.jsonl")
    args = parser.parse_args(argv)
    if args.budget <= 0:
        parser.error("--budget must be positive")
    result = {"shop": "", "receipt": None, "stars": 2, "review": "Could not start the trade."}
    exit_code = 0
    order = {}
    stage = "selection_setup"
    trade_started = False
    try:
        deadline = time.monotonic() + RUN_SECONDS
        order = read_json(args.order) if args.order else {}
        if order and (order.get("item") != args.item or order.get("budget") != args.budget):
            raise BuyerError("Order file and command arguments disagree")
        shops, memory = read_shops(args.shops), read_memory(args.memory, args.shops.parent / "payments.jsonl",
                                                         ROOT / "logs" / "settled-deals.jsonl")
        config = load_policy()
        catalog = None
        if order.get("round", 0) >= 3:
            checkout_policy(order, config)
            catalog = read_json(args.shops.parent / "catalog.json")
            if not isinstance(catalog, dict):
                raise BuyerError("Market catalog must be a SKU mapping")
            shops = stocked_shops(shops, order)
        stage = "shop_selection"
        selection = {}
        shop, reason = choose_shop(shops, memory, item=args.item, budget=args.budget,
                                   deadline=min(deadline, time.monotonic() + 90), policy=config, order=order,
                                   catalog=catalog, market_dir=args.shops.parent, selection_state=selection)
        result["shop"] = shop["url"]
        sku = shop.get("selected_sku") or args.item
        product = catalog.get(sku) if catalog is not None else None
        policy = buying_terms(args.budget, product=product, order=order, config=config)
        if shop.get("price_reference"):
            policy["price_reference"] = shop["price_reference"]
        log("PURCHASE POLICY", policy)  # local evidence only; never sent to the shop
        with httpx.Client(trust_env=False) as client:
            trade_started = True
            attempt_status = {}
            result = trade_and_record(shop, item=sku, budget=args.budget,
                                      memory_path=args.memory, reason=reason,
                                      seconds=deadline - time.monotonic(), client=client, order=order, policy=policy,
                                      attempt_status=attempt_status, market_dir=args.shops.parent)
            remaining = remaining_retry_decisions(attempt_status,
                max_decisions=config["max_decisions"], seconds_left=deadline - time.monotonic())
            alternative = (alternative_shop(selection, shop, order)
                           if order.get("round", 0) >= 3 and remaining else None)
            if alternative is not None and deadline - time.monotonic() >= 20:
                shop, reason = alternative
                sku = shop["selected_sku"]
                policy = buying_terms(args.budget, product=catalog.get(sku), order=order, config=config)
                policy["max_decisions"] = remaining
                if shop["price_reference"]:
                    policy["price_reference"] = shop["price_reference"]
                log("ALTERNATIVE SHOP", {"name": shop["name"], "sku": sku,
                    "reason": "confirmed_no_receipt_end", "decisions_left": remaining})
                log("PURCHASE POLICY", policy)
                result = trade_and_record(shop, item=sku, budget=args.budget,
                    memory_path=args.memory, reason=reason, seconds=deadline - time.monotonic(),
                    client=client, order=order, policy=policy, market_dir=args.shops.parent)
    except (BuyerError, HarnessError, OSError, ValueError) as exc:
        result["review"] = f"Buyer could not complete the order: {exc}"
        exit_code = 1
        log("MARKET BUYER ERROR", str(exc))
        if not trade_started:
            # Keep selection-only rows out of the name-indexed trading memory.
            # Optional observability must not alter the trade result.
            try:
                events = args.memory.parent / "buyer-events.jsonl"
                events.parent.mkdir(parents=True, exist_ok=True)
                reason_code = "unknown"
                if isinstance(exc, HarnessError):
                    reason_code = "model_timeout" if str(exc).startswith(
                        ("Harness timed out;", "Harness deadline expired;")) else "model_error"
                with events.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"version": 1, "event": "selection_failure",
                        "time": datetime.now(timezone.utc).isoformat(),
                        "order": order.get("order") if isinstance(order, dict) else None,
                        "round": order.get("round") if isinstance(order, dict) else None,
                        "stage": stage, "reason": reason_code,
                        "exception_class": type(exc).__name__}) + "\n")
            except (OSError, ValueError, TypeError):
                pass
    print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
