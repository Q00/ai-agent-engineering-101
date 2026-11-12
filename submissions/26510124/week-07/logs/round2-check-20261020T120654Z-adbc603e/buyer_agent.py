"""A2A buyer: Python transports messages; Claude decides every price and exit.

    python buyer_agent.py --url http://127.0.0.1:9999 --item lamp --budget 120

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

import httpx

from harness import HarnessError, buyer_decision


VERSION_HEADERS = {"A2A-Version": "1.0"}
TERMINAL_STATES = {
    "TASK_STATE_COMPLETED", "TASK_STATE_REJECTED", "TASK_STATE_CANCELED",
    "TASK_STATE_FAILED",
}
MAX_DECISIONS = 12
RUN_SECONDS = 270


class BuyerError(RuntimeError):
    """A protocol, safety, or execution failure; no fallback purchase is made."""


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
        prices = []
        for artifact in task.get("artifacts", []):
            for part in artifact.get("parts", []):
                data = part.get("data")
                if isinstance(data, dict) and "price" in data:
                    prices.append(data["price"])
        if len(prices) != 1:
            raise BuyerError("Completed task must contain exactly one numeric receipt price")
        price = prices[0]
        if (type(price) not in (int, float) or not math.isfinite(price)
                or not 0 < price <= self.budget or price != self.authorized_price):
            raise BuyerError("Receipt price exceeds budget or differs from the authorized offer")
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", type=int, required=True)
    args = parser.parse_args()
    if args.budget <= 0:
        parser.error("--budget must be positive")
    try:
        with httpx.Client(trust_env=False) as client:
            Buyer(client, url=args.url, item=args.item, budget=args.budget).run()
    except (BuyerError, HarnessError) as exc:
        print(f"BUYER ERROR: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
