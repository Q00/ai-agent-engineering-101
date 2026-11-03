"""Market buyer with name-based memory and the week-06 negotiation loop.

    python market_buyer.py --shops .market/shops.json --item lamp --budget 120
The 270-second deadline and 12-decision cap are operational error limits, not a
negotiation strategy. Hitting either fails visibly rather than inventing an offer.
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
from buyer_policy import buying_terms, checkout_policy, load_policy
from buyer_selection import shortlist, offer_fingerprint, reuse_failures
from buyer_context import compact_dialogue


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
            result.append({**shop, "offers": offers[:5]})
    if not result:
        raise BuyerError("No shop offers an acceptable SKU in stock")
    return result


def acceptance(order):
    return checkout_policy(order)


def choose_shop(shops, memory, *, item, budget, deadline, policy=None, order=None, catalog=None):
    order = order or {}
    shops, cooldown = reuse_failures(shops, memory, item=item, budget=budget,
                                     config=policy or load_policy(), order=order, catalog=catalog)
    candidates = shortlist(shops, memory, seed=order.get("order") or f"{item}:{int(time.time() // 900)}",
                           limit=(policy or {}).get("selection_max_candidates", 6),
                           round_number=order.get("round"))
    if not candidates:
        raise BuyerError("No candidate shops")
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
    context = {"item": item, "budget": budget, "candidates": candidates, "purchase_policy": policy}
    log("SELECTION CONTEXT", {"eligible_shops": len(shops), "candidates": len(candidates),
                              "context_chars": len(json.dumps(context, ensure_ascii=False)),
                              **cooldown,
                              "exploration": [c["name"] for c in candidates if c["selection_role"] == "exploration"]})
    choice = decide("market_select.txt", context, validate, timeout=min(45, remaining), prefix="market-select")
    log("SHOP SELECTION", choice)
    shop = next(s for s in shops if s["name"] == choice["name"])
    return {**shop, "selected_sku": choice.get("sku")}, choice["reason"]


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
    def __init__(self, client, *, url, item, budget, seconds=RUN_SECONDS, policy=None):
        self.client = client
        self.base_url = url.rstrip("/")
        self.endpoint = None
        self.item = item
        self.budget = budget
        self.policy = policy or buying_terms(budget)
        self.price_limit = min(budget, self.policy["price_limit"])
        self.no_deal_reason = ""
        self.deadline = time.monotonic() + seconds
        self.task_id = None
        self.terminal = False
        self.history = []
        self.authorized_price = None
        self.receipt = None
        self.observed_receipt = None

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
        self.terminal = task["status"].get("state") in TERMINAL_STATES
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
        if task["status"].get("state") != "TASK_STATE_CANCELED":
            raise BuyerError("Seller did not confirm cancellation")

    def validate_decision(self, decision, seller_message):
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
            raise PolicyRefusal("Model price exceeds the owner's purchasing limit")
        if (self.authorized_price is not None
                and price > self.authorized_price + self.policy["max_concession"]):
            raise PolicyRefusal("Model concession exceeds the per-turn limit")
        if action == "accept" and price != last_asking_price(seller_message):
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
            self.no_deal_reason = str(exc)
            if self.task_id is not None and not self.terminal:
                try:
                    self.cancel(cleanup=True)
                except BuyerError as cleanup:
                    log("CLEANUP ERROR", str(cleanup))
            log("POLICY WALK AWAY", self.no_deal_reason)
            return "no_deal"
        except (BuyerError, HarnessError):
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
        task = self.send(f"I want to buy a {self.item}")
        decisions = 0
        while True:
            self.remaining()
            state = task["status"].get("state")
            text = message_text(task)
            if text and state == "TASK_STATE_INPUT_REQUIRED":
                self.history.append({"role": "seller", "text": text})
            if state == "TASK_STATE_COMPLETED":
                self.verify_receipt(task)
                return "receipt_issued"
            if state in {"TASK_STATE_REJECTED", "TASK_STATE_CANCELED"}:
                log("NO DEAL", {"state": state, "taskId": self.task_id})
                return "no_deal"
            if state in {"TASK_STATE_SUBMITTED", "TASK_STATE_WORKING"}:
                time.sleep(min(0.25, self.remaining()))
                task = self.rpc("GetTask", {"id": self.task_id})
                continue
            if state != "TASK_STATE_INPUT_REQUIRED":
                raise BuyerError(f"Cannot negotiate in seller state {state}: {text}")
            if decisions >= min(MAX_DECISIONS, self.policy["max_decisions"]):
                raise PolicyRefusal("Negotiation ended at the owner's turn limit without a satisfactory agreement")
            compact = compact_dialogue(self.history, text, last_asking_price)
            log("NEGOTIATION CONTEXT", {"decision": decisions + 1,
                                        "context_chars": len(json.dumps(compact, ensure_ascii=False))})
            decision = buyer_decision(item=self.item, budget=self.budget,
                                      **compact,
                                      timeout=min(90, self.remaining()), policy=self.policy,
                                      current_ask=last_asking_price(text))
            decisions += 1
            log("BUYER DECISION", decision)
            self.validate_decision(decision, text)
            self.remaining()
            if decision["action"] == "walk_away":
                self.no_deal_reason = decision["reason"]
                self.cancel()
                log("NO DEAL", {"reason": decision["reason"], "taskId": self.task_id})
                return "no_deal"
            # The wire contract uses a numeric offer for both acceptance and
            # counteroffers. The model explicitly chooses that action and price.
            self.authorized_price = decision["price"]
            task = self.send(str(self.authorized_price))


def trade_and_record(shop, *, item, budget, memory_path, reason, seconds, client, order=None, policy=None):
    policy = policy or buying_terms(budget, order=order)
    buyer = Buyer(client, url=shop["url"], item=item, budget=budget, seconds=seconds, policy=policy)
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
        "observed_receipt": buyer.observed_receipt,
        "stars": stars, "review": review,
    }
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    with memory_path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
    return {"shop": shop["url"], "receipt": buyer.receipt, "stars": stars, "review": review,
            **({"accept": policy["accept"]} if order and order.get("round", 0) >= 3 else {})}


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
        shop, reason = choose_shop(shops, memory, item=args.item, budget=args.budget,
                                   deadline=min(deadline, time.monotonic() + 90), policy=config, order=order, catalog=catalog)
        result["shop"] = shop["url"]
        sku = shop.get("selected_sku") or args.item
        product = catalog.get(sku) if catalog is not None else None
        policy = buying_terms(args.budget, product=product, order=order, config=config)
        log("PURCHASE POLICY", policy)  # local evidence only; never sent to the shop
        with httpx.Client(trust_env=False) as client:
            result = trade_and_record(shop, item=sku, budget=args.budget,
                                      memory_path=args.memory, reason=reason,
                                      seconds=deadline - time.monotonic(), client=client, order=order, policy=policy)
    except (BuyerError, HarnessError, OSError, ValueError) as exc:
        result["review"] = f"Buyer could not complete the order: {exc}"
        exit_code = 1
        log("MARKET BUYER ERROR", str(exc))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
