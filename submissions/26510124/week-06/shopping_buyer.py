"""Challenge 2: discover shops, model-plan allocations, negotiate concurrently.

The immutable allocation ledger bounds total exposure before any offer is sent.
Each negotiation keeps exact facts outside its model-written 800-character
summary and two recent turns. Raw traffic and all model attempts remain in logs.
"""
import argparse
import asyncio
from collections import Counter
from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path
import sys
import time
import uuid

import httpx

from harness import HarnessError, decide, parse_object


HEADERS = {"A2A-Version": "1.0"}
TERMINAL = {"TASK_STATE_COMPLETED", "TASK_STATE_REJECTED", "TASK_STATE_CANCELED", "TASK_STATE_FAILED"}
MAX_ROUNDS = 12
SUMMARY_LIMIT = 800
RECENT_TURNS = 2


class ShoppingError(RuntimeError):
    """Protocol or invariant failure; do not invent a substitute purchase."""


def emit(event, **details):
    print(json.dumps({"event": event, "at": time.time(), **details}, ensure_ascii=False), flush=True)


def positive_number(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def validate_inputs(directory, shopping_list, budget):
    if type(budget) is not int or budget <= 0:
        raise ShoppingError("Total budget must be a positive integer")
    shops = directory.get("shops") if isinstance(directory, dict) else None
    items = shopping_list.get("items") if isinstance(shopping_list, dict) else None
    if not isinstance(shops, list) or not shops or not isinstance(items, list) or not items:
        raise ShoppingError("Directory and shopping list must be nonempty lists")
    for rows, label in [(shops, "shop"), (items, "item")]:
        ids = [row.get("id") if isinstance(row, dict) else None for row in rows]
        if any(not isinstance(value, str) or not value.strip() for value in ids) or len(ids) != len(set(ids)):
            raise ShoppingError(f"Every {label} must have a unique nonempty id")
    for shop in shops:
        if not isinstance(shop.get("url"), str) or not shop["url"].startswith(("http://", "https://")):
            raise ShoppingError("Every shop must have an HTTP(S) URL")
    for item in items:
        if not isinstance(item.get("description"), str) or not item["description"].strip():
            raise ShoppingError("Each item needs a natural-language description")
        constraints = item.get("constraints")
        if not isinstance(constraints, dict) or not constraints:
            raise ShoppingError("Each item needs structured constraints to verify the purchased product")
        for key, value in constraints.items():
            if not isinstance(key, str) or not key:
                raise ShoppingError("Constraint names must be nonempty strings")
            if key.endswith(("_min", "_max")):
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ShoppingError("Numeric bounds must be finite numbers")
            elif type(value) not in (str, int, float, bool) or (isinstance(value, float) and not math.isfinite(value)):
                raise ShoppingError("Equality constraints must be scalar values")
    return shops, items


def validate_plan(output, item_ids, shop_ids, budget):
    plan = parse_object(output)
    assignments = plan.get("assignments")
    if not isinstance(assignments, list) or len(assignments) != len(item_ids):
        raise HarnessError("Plan must assign every shopping item exactly once")
    seen, allocated = set(), 0
    for entry in assignments:
        if not isinstance(entry, dict):
            raise HarnessError("Each plan assignment must be an object")
        item_id, shop_id, amount = entry.get("item_id"), entry.get("shop_id"), entry.get("budget")
        if not isinstance(item_id, str) or item_id not in item_ids or item_id in seen:
            raise HarnessError("Unknown or duplicate shopping item in plan")
        if not isinstance(shop_id, str) or shop_id not in shop_ids:
            raise HarnessError("Unknown shop in plan")
        if type(amount) is not int or amount <= 0:
            raise HarnessError("Each allocation must be a positive integer")
        seen.add(item_id)
        allocated += amount
    if seen != set(item_ids) or allocated > budget:
        raise HarnessError("Plan must cover the list without exceeding the total budget")
    return plan


def verify_product(product, constraints):
    if not isinstance(product, dict) or any(not isinstance(product.get(k), str) or not product[k].strip()
                                           for k in ("id", "name", "description")):
        raise ShoppingError("Seller must identify the product with id, name and description")
    for key, expected in constraints.items():
        if key.endswith(("_min", "_max")):
            attribute, operation = key.rsplit("_", 1)
            actual = product.get(attribute)
            if (type(actual) not in (int, float) or not math.isfinite(actual)
                    or (operation == "min" and actual < expected)
                    or (operation == "max" and actual > expected)):
                raise ShoppingError(f"Product violates constraint {key}={expected}")
        else:
            actual = product.get(key)
            matches = (actual.casefold() == expected.casefold() if isinstance(actual, str) and isinstance(expected, str)
                       else type(actual) is type(expected) and actual == expected)
            if not matches:
                raise ShoppingError(f"Product violates constraint {key}={expected}")


def validate_decision(output, budget, ask):
    decision = parse_object(output)
    action = decision.get("action")
    if action not in {"offer", "accept", "walk_away"}:
        raise HarnessError("Action must be offer, accept or walk_away")
    summary = decision.get("summary")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > SUMMARY_LIMIT:
        raise HarnessError(f"Summary must contain 1 to {SUMMARY_LIMIT} characters")
    if action != "walk_away":
        price = decision.get("price")
        if type(price) is not int or not 0 < price <= budget:
            raise HarnessError("Offer must be a positive integer within this item's allocation")
        if action == "accept" and price != ask:
            raise HarnessError("Acceptance must use exactly the current seller ask")
    return decision


class AllocationLedger:
    """Synchronous mutations are atomic on the single asyncio event-loop thread.

    All allocations are reserved in one validated transaction before starting
    concurrent negotiations; unused money is never silently reassigned.
    """
    def __init__(self, budget):
        self.budget = budget
        self.allocations = {}
        self.receipts = {}

    def reserve(self, plan, item_ids, shop_ids):
        if self.allocations:
            raise ShoppingError("Shopping allocations were already reserved")
        validated = validate_plan(json.dumps(plan), item_ids, shop_ids, self.budget)
        allocations = {row["item_id"]: dict(row) for row in validated["assignments"]}
        self.allocations = allocations
        emit("allocations_reserved", allocations=allocations, total_reserved=sum(x["budget"] for x in allocations.values()), budget=self.budget)

    def purchase(self, item_id, receipt):
        if item_id in self.receipts:
            raise ShoppingError("Duplicate purchase for the same shopping item")
        allocation = self.allocations.get(item_id)
        price = receipt.get("price")
        if (allocation is None or not positive_number(price) or price > allocation["budget"]
                or receipt.get("shopping_item_id") != item_id or receipt.get("shop_id") != allocation["shop_id"]):
            raise ShoppingError("Receipt does not match its reserved allocation")
        if sum(row["price"] for row in self.receipts.values()) + price > self.budget:
            raise ShoppingError("Aggregate receipt price exceeds total budget")
        self.receipts[item_id] = receipt


@dataclass
class NegotiationState:
    item_id: str
    shop_id: str
    budget: int
    task_id: str | None = None
    product: dict | None = None
    latest_ask: int | float | None = None
    last_offer: int | None = None
    round: int = 0
    summary: str = ""
    recent: list = field(default_factory=list)
    terminal: bool = False

    def remember(self, role, text):
        self.recent.append({"role": role, "text": text})
        self.recent = self.recent[-RECENT_TURNS:]

    def context(self, item):
        return {"shopping_item": item, "exact_state": {
            "taskId": self.task_id, "shop_id": self.shop_id, "allocated_budget": self.budget,
            "product": self.product, "current_ask": self.latest_ask,
            "last_offer": self.last_offer, "round": self.round,
        }, "summary": self.summary, "recent_turns": list(self.recent)}


class ShoppingBuyer:
    def __init__(self, client, directory, shopping_list, budget, *, seconds=270):
        self.client = client
        self.shops, self.items = validate_inputs(directory, shopping_list, budget)
        self.budget = budget
        self.deadline = time.monotonic() + seconds
        self.ledger = AllocationLedger(budget)
        self.cards = {}
        self.states = {}
        self.active = 0
        self.peak_active = 0
        self.active_shops = Counter()
        self.peak_distinct_shops = 0

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ShoppingError("Shopping deadline exceeded")
        return remaining

    async def request(self, method, url, *, body=None, cleanup=False):
        timeout = 3 if cleanup else min(90, self.remaining())
        emit("http_request", method=method, url=url, headers=HEADERS, body=body)
        try:
            response = await asyncio.wait_for(self.client.request(method, url, headers=HEADERS, json=body, timeout=timeout), timeout)
            emit("http_response", method=method, url=url, status=response.status_code, body=response.text)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError, TimeoutError) as exc:
            raise ShoppingError(f"{method} {url} failed: {exc}") from exc
        if not isinstance(data, dict):
            raise ShoppingError("A2A response must be a JSON object")
        return data

    async def discover(self, shop):
        card = await self.request("GET", shop["url"].rstrip("/") + "/.well-known/agent-card.json")
        interfaces = card.get("supportedInterfaces")
        if not isinstance(interfaces, list):
            raise ShoppingError("Agent Card has no supportedInterfaces")
        endpoint = next((entry.get("url") for entry in interfaces if isinstance(entry, dict)
                         and entry.get("protocolBinding") == "JSONRPC" and entry.get("protocolVersion") == "1.0"
                         and isinstance(entry.get("url"), str) and entry["url"].startswith(("http://", "https://"))), None)
        if endpoint is None or not isinstance(card.get("skills"), list) or not card["skills"]:
            raise ShoppingError("Agent Card needs skills and an A2A 1.0 JSONRPC interface")
        self.cards[shop["id"]] = {"shop_id": shop["id"], "name": card.get("name"), "description": card.get("description"),
                                  "skills": card["skills"], "endpoint": endpoint}
        emit("card_discovered", shop_id=shop["id"], skills=card["skills"])

    async def rpc(self, state, method, params, *, cleanup=False):
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}
        reply = await self.request("POST", self.cards[state.shop_id]["endpoint"], body=body, cleanup=cleanup)
        if reply.get("jsonrpc") != "2.0" or reply.get("id") != body["id"]:
            raise ShoppingError("JSON-RPC response version/id mismatch")
        if "error" in reply:
            raise ShoppingError(f"{method}: {reply['error']}")
        result = reply.get("result")
        task = result.get("task", result) if isinstance(result, dict) else None
        if not isinstance(task, dict) or not isinstance(task.get("status"), dict):
            raise ShoppingError("Seller must return an A2A Task")
        task_id = task.get("id")
        if not isinstance(task_id, str) or not task_id or state.task_id not in (None, task_id):
            raise ShoppingError("Seller taskId is missing or changed")
        state.task_id = task_id
        state.terminal = task["status"].get("state") in TERMINAL
        return task

    async def send(self, state, text, constraints=None):
        parts = [{"text": text}]
        if constraints is not None:
            parts.append({"data": {"constraints": constraints}})
        message = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": parts}
        if state.task_id is not None:
            message["taskId"] = state.task_id
        state.remember("buyer", text)
        return await self.rpc(state, "SendMessage", {"message": message})

    async def cancel(self, state, *, cleanup=False):
        task = await self.rpc(state, "CancelTask", {"id": state.task_id}, cleanup=cleanup)
        if task["status"].get("state") != "TASK_STATE_CANCELED":
            raise ShoppingError("Seller did not confirm cancellation")

    def read_offer(self, state, task, item):
        message = task["status"].get("message") or {}
        parts = message.get("parts", [])
        offers = [part["data"] for part in parts if isinstance(part, dict)
                  and isinstance(part.get("data"), dict) and "product" in part["data"] and "ask" in part["data"]]
        if len(offers) != 1:
            raise ShoppingError("Seller must send one structured product and ask")
        product, ask = offers[0]["product"], offers[0]["ask"]
        verify_product(product, item["constraints"])
        if state.product is not None and product != state.product:
            raise ShoppingError("Seller changed the selected product during negotiation")
        if not positive_number(ask):
            raise ShoppingError("Seller asking price must be positive and finite")
        state.product, state.latest_ask = product, ask
        text = " ".join(part["text"] for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str))
        state.remember("seller", text)

    def receipt(self, state, task, item):
        receipts = [part["data"] for artifact in task.get("artifacts", []) for part in artifact.get("parts", [])
                    if isinstance(part, dict) and isinstance(part.get("data"), dict) and "price" in part["data"]]
        if len(receipts) != 1 or state.product is None or state.last_offer is None:
            raise ShoppingError("Completed task needs exactly one authorized product receipt")
        data = receipts[0]
        price, product = data.get("price"), data.get("product")
        verify_product(product, item["constraints"])
        if product != state.product or data.get("item") != state.product["id"]:
            raise ShoppingError("Receipt product differs from the authorized product")
        if not positive_number(price) or price > state.budget or price != state.last_offer:
            raise ShoppingError("Receipt price differs from the authorized offer or exceeds its allocation")
        receipt = {"shopping_item_id": state.item_id, "shop_id": state.shop_id, "taskId": state.task_id,
                   "price": price, "product": product}
        self.ledger.purchase(state.item_id, receipt)
        emit("purchase", **receipt)
        return receipt

    async def negotiate(self, item, allocation):
        state = NegotiationState(item["id"], allocation["shop_id"], allocation["budget"])
        self.states[item["id"]] = state
        self.active += 1
        self.active_shops[state.shop_id] += 1
        self.peak_active = max(self.peak_active, self.active)
        self.peak_distinct_shops = max(self.peak_distinct_shops, len(self.active_shops))
        emit("negotiation_started", item_id=state.item_id, shop_id=state.shop_id, active=self.active, distinct_shops=len(self.active_shops))
        try:
            task = await self.send(state, item["description"], item["constraints"])
            while True:
                self.remaining()
                status = task["status"].get("state")
                if status == "TASK_STATE_COMPLETED":
                    return self.receipt(state, task, item)
                if status in {"TASK_STATE_CANCELED", "TASK_STATE_REJECTED"}:
                    emit("unmet_item", item_id=state.item_id, state=status)
                    return None
                if status in {"TASK_STATE_SUBMITTED", "TASK_STATE_WORKING"}:
                    await asyncio.sleep(min(0.2, self.remaining()))
                    task = await self.rpc(state, "GetTask", {"id": state.task_id})
                    continue
                if status != "TASK_STATE_INPUT_REQUIRED":
                    raise ShoppingError(f"Seller failed or returned unsupported state: {status}")
                self.read_offer(state, task, item)
                if state.round >= MAX_ROUNDS:
                    raise ShoppingError("Negotiation round limit reached")
                context = state.context(item)
                emit("compact_context", item_id=state.item_id, context=context,
                     summary_chars=len(state.summary), recent_turns=len(state.recent))
                decision = await asyncio.to_thread(decide, "shopping_negotiate.txt", context,
                    lambda output: validate_decision(output, state.budget, state.latest_ask),
                    timeout=min(90, self.remaining()), prefix="shopping-buyer")
                self.remaining()
                # Revalidate before effects, including when tests replace the harness.
                validate_decision(json.dumps(decision), state.budget, state.latest_ask)
                state.round += 1
                previous_summary = state.summary
                state.summary = decision["summary"]
                emit("buyer_decision", item_id=state.item_id, decision=decision)
                emit("summary_updated", item_id=state.item_id, round=state.round,
                     previous=previous_summary, summary=state.summary, summary_chars=len(state.summary))
                if decision["action"] == "walk_away":
                    await self.cancel(state)
                    emit("unmet_item", item_id=state.item_id, reason=decision["reason"])
                    return None
                state.last_offer = decision["price"]
                task = await self.send(state, str(state.last_offer))
        except (ShoppingError, HarnessError, asyncio.CancelledError) as exc:
            if state.task_id is not None and not state.terminal:
                try:
                    await self.cancel(state, cleanup=True)
                except (ShoppingError, asyncio.CancelledError) as cleanup_error:
                    emit("cleanup_error", item_id=state.item_id, error=str(cleanup_error))
            if isinstance(exc, asyncio.CancelledError):
                raise
            emit("negotiation_error", item_id=state.item_id, error=str(exc))
            return {"error": str(exc), "shopping_item_id": state.item_id}
        finally:
            self.active -= 1
            self.active_shops[state.shop_id] -= 1
            if self.active_shops[state.shop_id] == 0:
                del self.active_shops[state.shop_id]
            emit("negotiation_finished", item_id=state.item_id, active=self.active, state=asdict(state))

    async def run(self):
        await asyncio.gather(*(self.discover(shop) for shop in self.shops))
        emit("directory_read", count=len(self.cards))
        item_ids, shop_ids = {item["id"] for item in self.items}, set(self.cards)
        plan = await asyncio.to_thread(decide, "shopping_plan.txt", {
            "shops": [self.cards[shop["id"]] for shop in self.shops], "shopping_list": self.items, "total_budget": self.budget,
        }, lambda output: validate_plan(output, item_ids, shop_ids, self.budget),
            timeout=min(90, self.remaining()), prefix="shopping-plan")
        self.remaining()
        self.ledger.reserve(plan, item_ids, shop_ids)
        emit("shopping_plan", plan=plan)
        outcomes = await asyncio.gather(*(self.negotiate(item, self.ledger.allocations[item["id"]]) for item in self.items))
        receipts = list(self.ledger.receipts.values())
        counts = Counter(receipt["shopping_item_id"] for receipt in receipts)
        duplicates = sorted(item for item, count in counts.items() if count > 1)
        missing = sorted(item_ids - counts.keys())
        total_spent = sum(receipt["price"] for receipt in receipts)
        errors = [outcome for outcome in outcomes if isinstance(outcome, dict) and "error" in outcome]
        result = {"passed": not missing and not duplicates and not errors and total_spent <= self.budget,
                  "receipts": receipts, "total_spent": total_spent, "budget": self.budget,
                  "missing": missing, "duplicates": duplicates, "errors": errors,
                  "cards_read": len(self.cards), "peak_concurrent_negotiations": self.peak_active,
                  "peak_distinct_shops": self.peak_distinct_shops, "plan": plan,
                  "final_states": {item_id: asdict(state) for item_id, state in self.states.items()}}
        emit("shopping_result", result=result)
        return result


async def async_main(args):
    directory = json.loads(Path(args.directory).read_text(encoding="utf-8"))
    shopping_list = json.loads(Path(args.list).read_text(encoding="utf-8"))
    # Reserve an exclusive output path before any model/network effects.
    with Path(args.output).open("x", encoding="utf-8") as output:
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                result = await ShoppingBuyer(client, directory, shopping_list, args.budget).run()
        except (ShoppingError, HarnessError) as exc:
            result = {"passed": False, "error": str(exc), "budget": args.budget}
            emit("shopping_error", **result)
        output.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return 0 if result["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--list", required=True)
    parser.add_argument("--budget", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.budget <= 0:
        parser.error("--budget must be positive")
    try:
        return asyncio.run(async_main(args))
    except (OSError, ValueError, ShoppingError) as exc:
        print(f"SHOPPING ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
