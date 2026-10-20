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


VERSION_HEADERS = {"A2A-Version": "1.0"}
TERMINAL_STATES = {
    "TASK_STATE_COMPLETED", "TASK_STATE_REJECTED", "TASK_STATE_CANCELED",
    "TASK_STATE_FAILED",
}
MAX_DECISIONS = 12
RUN_SECONDS = 270
ROOT = Path(__file__).resolve().parent
SELECTION_BATCH = 12


class BuyerError(RuntimeError):
    """A protocol, safety, or execution failure; no fallback purchase is made."""


class ReceiptMismatch(BuyerError):
    """The completed receipt does not match the authorized purchase."""


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


def read_memory(path):
    if not path.exists():
        return []
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict) or not isinstance(record.get("name"), str):
            raise BuyerError(f"Invalid memory record on line {number}")
        records.append(record)
    return records


def shop_evidence(shop, memory):
    """Bound prompt size while keeping counts of older negative experiences."""
    reviews = [r for r in shop.get("reviews", [])[:10] if isinstance(r, dict)]
    own = [r for r in memory if r["name"] == shop["name"]]
    stars = [r["stars"] for r in reviews if type(r.get("stars")) is int and 1 <= r["stars"] <= 5]
    return {
        "name": shop["name"],
        "reviews": {
            "count": len(reviews),
            "low_star_count": sum(s <= 2 for s in stars),
            "average_stars": sum(stars) / len(stars) if stars else None,
            "recent_newest_first": [
                {"stars": r.get("stars"), "round": r.get("round"),
                 "review": str(r.get("review", ""))[:400]} for r in reviews[:3]
            ],
        },
        "own_memory": {
            "trades": len(own),
            "purchased": sum(r.get("result") == "purchased" for r in own),
            "receipt_mismatches": sum(r.get("result") == "receipt_mismatch" for r in own),
            "no_deal": sum(r.get("result") == "no_deal" for r in own),
            "errors": sum(r.get("result") == "error" for r in own),
            "recent_newest_first": [
                {key: (str(r.get(key, ""))[:400] if key == "detail" else r.get(key))
                 for key in ("item", "price", "result", "detail")}
                for r in reversed(own[-3:])
            ],
        },
    }


def choose_shop(shops, memory, *, item, budget, deadline):
    candidates = [shop_evidence(s, memory) for s in shops]
    choice = None
    while True:
        finalists = []
        for offset in range(0, len(candidates), SELECTION_BATCH):
            batch = candidates[offset:offset + SELECTION_BATCH]
            names = {s["name"] for s in batch}

            def validate(output):
                decision = parse_object(output)
                if not isinstance(decision.get("name"), str) or decision["name"] not in names:
                    raise HarnessError("Choose one exact name from the supplied candidates")
                return decision

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BuyerError("Shop selection deadline exceeded")
            choice = decide("market_select.txt", {
                "item": item, "budget": budget, "candidates": batch,
            }, validate, timeout=min(45, remaining), prefix="market-select")
            log("SHOP SELECTION", choice)
            finalists.append(next(s for s in batch if s["name"] == choice["name"]))
        if len(finalists) == 1:
            return next(s for s in shops if s["name"] == finalists[0]["name"]), choice["reason"]
        candidates = finalists


def log(label, value):
    """The trade test captures stdout; keep requests and responses unchanged."""
    print(f"{label}: {json.dumps(value, ensure_ascii=False)}", flush=True)


def message_text(task):
    message = task.get("status", {}).get("message") or {}
    return " ".join(part["text"] for part in message.get("parts", [])
                    if isinstance(part, dict) and isinstance(part.get("text"), str))


def last_asking_price(text):
    """Our text contract writes the seller's asking price as the last number."""
    numbers = re.findall(r"-?\d+(?:\.\d+)?", text)
    return float(numbers[-1]) if numbers else None


class Buyer:
    def __init__(self, client, *, url, item, budget, seconds=RUN_SECONDS):
        self.client = client
        self.base_url = url.rstrip("/")
        self.endpoint = None
        self.item = item
        self.budget = budget
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
                or price != self.authorized_price):
            raise ReceiptMismatch(
                f"Authorized {self.authorized_price}, receipt price {price!r}, budget {self.budget}")
        if self.observed_receipt.get("item") != self.item:
            raise ReceiptMismatch("Receipt item differs from the ordered item")
        if "task" in self.observed_receipt and self.observed_receipt["task"] != self.task_id:
            raise ReceiptMismatch("Receipt task differs from the negotiation taskId")
        # Keep the exact data Part, including sig_seller and unknown extra fields.
        self.receipt = self.observed_receipt
        log("PURCHASE", {"item": self.item, "price": price,
                         "budget": self.budget, "taskId": self.task_id})

    def run(self):
        try:
            return self.negotiate()
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
            if text:
                self.history.append({"role": "seller", "text": text})
            if state == "TASK_STATE_COMPLETED":
                self.verify_receipt(task)
                return "purchased"
            if state in {"TASK_STATE_REJECTED", "TASK_STATE_CANCELED"}:
                log("NO DEAL", {"state": state, "taskId": self.task_id})
                return "no_deal"
            if state in {"TASK_STATE_SUBMITTED", "TASK_STATE_WORKING"}:
                time.sleep(min(0.25, self.remaining()))
                task = self.rpc("GetTask", {"id": self.task_id})
                continue
            if state != "TASK_STATE_INPUT_REQUIRED":
                raise BuyerError(f"Cannot negotiate in seller state {state}: {text}")
            if decisions >= MAX_DECISIONS:
                raise BuyerError("Operational decision limit reached; no invented fallback")
            decision = buyer_decision(item=self.item, budget=self.budget,
                                      seller_message=text, history=list(self.history),
                                      timeout=min(90, self.remaining()))
            decisions += 1
            log("BUYER DECISION", decision)
            self.validate_decision(decision, text)
            self.remaining()
            if decision["action"] == "walk_away":
                self.cancel()
                log("NO DEAL", {"reason": decision["reason"], "taskId": self.task_id})
                return "no_deal"
            # The wire contract uses a numeric offer for both acceptance and
            # counteroffers. The model explicitly chooses that action and price.
            self.authorized_price = decision["price"]
            task = self.send(str(self.authorized_price))


def trade_and_record(shop, *, item, budget, memory_path, reason, seconds, client):
    buyer = Buyer(client, url=shop["url"], item=item, budget=budget, seconds=seconds)
    detail = ""
    try:
        outcome = buyer.run()
    except ReceiptMismatch as exc:
        outcome, detail = "receipt_mismatch", str(exc)
    except (BuyerError, HarnessError) as exc:
        outcome, detail = "error", str(exc)
    if outcome == "purchased":
        stars, review = 5, f"Bought {item} at {buyer.receipt['price']}; receipt matched my authorized offer within budget {budget}."
    elif outcome == "no_deal":
        stars, review = 3, f"No deal for {item} within budget {budget}; no completed purchase."
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
        "observed_receipt": buyer.observed_receipt,
        "stars": stars, "review": review,
    }
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    with memory_path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
    return {"shop": shop["url"], "receipt": buyer.receipt, "stars": stars, "review": review}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shops", type=Path, required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", type=int, required=True)
    parser.add_argument("--memory", type=Path, default=ROOT / "data" / "market-memory.jsonl")
    args = parser.parse_args(argv)
    if args.budget <= 0:
        parser.error("--budget must be positive")
    result = {"shop": "", "receipt": None, "stars": 2, "review": "Could not start the trade."}
    exit_code = 0
    try:
        deadline = time.monotonic() + RUN_SECONDS
        shops, memory = read_shops(args.shops), read_memory(args.memory)
        shop, reason = choose_shop(shops, memory, item=args.item, budget=args.budget,
                                   deadline=min(deadline, time.monotonic() + 90))
        result["shop"] = shop["url"]
        with httpx.Client(trust_env=False) as client:
            result = trade_and_record(shop, item=args.item, budget=args.budget,
                                      memory_path=args.memory, reason=reason,
                                      seconds=deadline - time.monotonic(), client=client)
    except (BuyerError, HarnessError, OSError, ValueError) as exc:
        result["review"] = f"Buyer could not complete the order: {exc}"
        exit_code = 1
        log("MARKET BUYER ERROR", str(exc))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
