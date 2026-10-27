"""Model-driven A2A v1.0 buyer for the Week 06 trade test."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

from harness import (BuyerDecisionContext, HarnessConfig, HarnessError,
                     NegotiationTurn, decide_buyer)
from protocol import A2A_VERSION_HEADER

INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
COMPLETED = "TASK_STATE_COMPLETED"
REJECTED = "TASK_STATE_REJECTED"
CANCELED = "TASK_STATE_CANCELED"
MAX_ROUNDS = 8
RUN_TIMEOUT_SECONDS = 240
HTTP_TIMEOUT_SECONDS = 20


class BuyerError(RuntimeError):
    """Raised for unsafe, malformed, or failed buyer operations."""


@dataclass(frozen=True)
class BuyerOutcome:
    outcome: str
    task_id: str | None = None
    price: int | None = None
    receipt: dict | None = None


def validate_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise BuyerError("Seller URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise BuyerError("Seller URL must not contain credentials, query, or fragment")
    return value.rstrip("/")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Week 06 A2A buyer")
    parser.add_argument("--url", required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", required=True, type=int)
    args = parser.parse_args(argv)
    if args.budget <= 0:
        parser.error("--budget must be a positive integer")
    try:
        args.url = validate_url(args.url)
    except BuyerError as exc:
        parser.error(str(exc))
    return args


class BuyerClient:
    def __init__(self, base_url: str, client: httpx.Client | None = None) -> None:
        self.base_url = validate_url(base_url)
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
        self.rpc_url: str | None = None
        self.history: list[NegotiationTurn] = []
        self.task_id: str | None = None
        self.last_offer: int | None = None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "BuyerClient":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    @staticmethod
    def _json(response: httpx.Response) -> dict:
        try:
            response.raise_for_status()
            value = response.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            raise BuyerError("Seller returned an invalid HTTP or JSON response") from exc
        if not isinstance(value, dict):
            raise BuyerError("Seller response must be a JSON object")
        return value

    def fetch_agent_card(self) -> dict:
        url = f"{self.base_url}/.well-known/agent-card.json"
        try:
            card = self._json(self.client.get(url))
        except httpx.HTTPError as exc:
            raise BuyerError("Agent Card request failed") from exc
        interfaces = card.get("supportedInterfaces")
        if not isinstance(interfaces, list):
            raise BuyerError("Agent Card has no supportedInterfaces")
        interface = next(
            (item for item in interfaces if isinstance(item, dict)
             and item.get("protocolBinding") == "JSONRPC"
             and str(item.get("protocolVersion")) == "1.0"),
            None,
        )
        if interface is None or not isinstance(interface.get("url"), str):
            raise BuyerError("Agent Card has no A2A v1.0 JSON-RPC interface")
        self.rpc_url = validate_url(interface["url"])
        return card

    def rpc(self, method: str, params: dict) -> dict:
        if self.rpc_url is None:
            raise BuyerError("Agent Card must be fetched before JSON-RPC calls")
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}
        try:
            response = self._json(self.client.post(self.rpc_url, headers=A2A_VERSION_HEADER, json=body))
        except httpx.HTTPError as exc:
            raise BuyerError("A2A request failed") from exc
        if "error" in response:
            raise BuyerError("Seller returned a JSON-RPC error")
        result = response.get("result")
        if not isinstance(result, dict):
            raise BuyerError("JSON-RPC response has no result object")
        task = result.get("task", result)
        if not isinstance(task, dict) or not isinstance(task.get("status"), dict):
            raise BuyerError("JSON-RPC result is not an A2A Task")
        return task

    def send_message(self, text: str, task_id: str | None = None) -> dict:
        message = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
        if task_id is not None:
            message["taskId"] = task_id
        return self.rpc("SendMessage", {"message": message})

    @staticmethod
    def _state(task: dict) -> str:
        state = task.get("status", {}).get("state")
        if not isinstance(state, str):
            raise BuyerError("Task has no valid state")
        return state

    @staticmethod
    def _message_text(task: dict) -> str:
        parts = task.get("status", {}).get("message", {}).get("parts", [])
        return " ".join(part.get("text", "") for part in parts if isinstance(part, dict))

    @classmethod
    def _ask(cls, task: dict) -> int:
        numbers = re.findall(r"\d+(?:\.\d+)?", cls._message_text(task))
        if not numbers:
            raise BuyerError("Input-required response has no asking price")
        return int(float(numbers[-1]))

    @staticmethod
    def _receipt(task: dict) -> dict:
        for artifact in task.get("artifacts", []):
            for part in artifact.get("parts", []):
                data = part.get("data")
                price = data.get("price") if isinstance(data, dict) else None
                if isinstance(price, (int, float)) and not isinstance(price, bool) and float(price).is_integer():
                    receipt = dict(data)
                    receipt["price"] = int(price)
                    return receipt
        raise BuyerError("Completed task has no integer-valued receipt price")

    @classmethod
    def _receipt_price(cls, task: dict) -> int:
        return int(cls._receipt(task)["price"])

    def _cancel_once(self) -> None:
        if self.task_id is None:
            return
        try:
            self.rpc("CancelTask", {"id": self.task_id})
        except (BuyerError, httpx.HTTPError):
            pass

    def _terminal_outcome(self, task: dict, budget: int) -> BuyerOutcome | None:
        """Return a terminal result without making another model or A2A call."""
        state = self._state(task)
        task_id = self.task_id or task.get("id")
        if state == COMPLETED:
            receipt = self._receipt(task)
            price = int(receipt["price"])
            if price > budget:
                raise BuyerError("Completed deal exceeds the budget")
            return BuyerOutcome("deal", task_id, price, receipt)
        if state == REJECTED:
            return BuyerOutcome("rejected", task_id)
        if state == CANCELED:
            return BuyerOutcome("canceled", task_id)
        return None

    def buy(self, item: str, budget: int) -> BuyerOutcome:
        if budget <= 0:
            raise BuyerError("Budget must be positive")
        deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
        self.fetch_agent_card()
        first_text = f"I want to buy a {item}"
        task = self.send_message(first_text)
        self.history.append(NegotiationTurn("buyer", first_text))

        for _ in range(MAX_ROUNDS):
            terminal = self._terminal_outcome(task, budget)
            if terminal is not None:
                return terminal
            state = self._state(task)
            if state != INPUT_REQUIRED:
                raise BuyerError("Seller returned an unsupported task state")
            task_id = task.get("id")
            if not isinstance(task_id, str) or not task_id:
                raise BuyerError("Input-required task has no taskId")
            if self.task_id is not None and task_id != self.task_id:
                raise BuyerError("Seller changed the taskId during negotiation")
            self.task_id = task_id
            if time.monotonic() >= deadline:
                self._cancel_once()
                raise BuyerError("Buyer exceeded its execution time limit")
            ask = self._ask(task)
            seller_text = self._message_text(task)
            self.history.append(NegotiationTurn("seller", seller_text, ask))
            remaining = max(1, int(deadline - time.monotonic()))
            context = BuyerDecisionContext(
                item=item, budget=budget, current_ask=ask, task_id=task_id,
                remaining_budget=budget, last_buyer_offer=self.last_offer,
                history=tuple(self.history),
            )
            try:
                decision = decide_buyer(context, HarnessConfig(timeout_seconds=min(120, remaining)))
            except HarnessError as exc:
                self._cancel_once()
                raise BuyerError("Buyer decision failed safely") from exc
            if decision["action"] == "walk_away":
                self._cancel_once()
                return BuyerOutcome("walked_away", task_id)
            price = decision["price"]
            if price > budget:
                self._cancel_once()
                raise BuyerError("Buyer decision exceeds the budget")
            self.last_offer = price
            offer_text = str(price)
            self.history.append(NegotiationTurn("buyer", offer_text, price))
            task = self.send_message(offer_text, task_id)

        # The final SendMessage already returned the seller's A2A response.
        # Inspect it once without another buyer decision, model call, or status
        # request so a deal completed on the last permitted round is retained.
        terminal = self._terminal_outcome(task, budget)
        if terminal is not None:
            return terminal
        if self._state(task) != INPUT_REQUIRED:
            raise BuyerError("Seller returned an unsupported task state")
        self._cancel_once()
        return BuyerOutcome("round_limit", self.task_id)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        with BuyerClient(args.url) as client:
            outcome = client.buy(args.item, args.budget)
        print(f"outcome={outcome.outcome} taskId={outcome.task_id or ''} price={outcome.price or ''}")
        return 0
    except (BuyerError, httpx.HTTPError):
        print("buyer failed safely", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
