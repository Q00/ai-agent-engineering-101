"""Deterministic B-original A2A buyer with fail-closed receipt checks."""
from __future__ import annotations

import argparse
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx


INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
COMPLETED = "TASK_STATE_COMPLETED"


class BuyerError(RuntimeError):
    pass


CandidateBuyerError = BuyerError


@dataclass(frozen=True)
class BuyerOutcome:
    """Compatibility shape retained for older local tests and reports."""

    outcome: str
    task_id: str | None = None
    price: int | None = None
    receipt: dict | None = None


@dataclass(frozen=True)
class Quote:
    shop_url: str
    item: str
    list_price: int
    ask: int
    task: dict


def validate_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise BuyerError("seller URL must be absolute HTTP(S)")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise BuyerError("seller URL contains unsupported components")
    return value.rstrip("/")


def list_price_from_catalog(path: str | Path, sku: str) -> int:
    try:
        catalog = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BuyerError("catalog is unavailable") from exc
    product = catalog.get(sku) if isinstance(catalog, dict) else None
    price = product.get("list_price") if isinstance(product, dict) else None
    if type(price) is not int or price <= 0:
        raise BuyerError("catalog has no valid list price")
    return price


def offer_for(ask: int, list_price: int, budget: int, turn: int, max_turns: int = 3) -> int:
    """B-original price path: 68% of list toward a 90%-of-list ceiling."""
    if not all(type(value) is int for value in (ask, list_price, budget, turn)):
        raise ValueError("prices and turn must be integers")
    if budget <= 0 or list_price <= 0 or max_turns <= 0 or not 0 <= turn < max_turns:
        raise ValueError("invalid negotiation bounds")
    ceiling = min(budget, round(list_price * 0.90))
    start = round(list_price * 0.68)
    proposed = round(start + (ceiling - start) * (turn + 1) / max_turns)
    return min(ask, budget, max(0, proposed))


def validate_receipt(receipt: dict, item: str, budget: int, final_offer: int) -> str | None:
    if receipt.get("item") != item:
        return "receipt_item_mismatch"
    price = receipt.get("price")
    if not isinstance(price, (int, float)) or isinstance(price, bool) or not float(price).is_integer():
        return "receipt_price_mismatch"
    price = int(price)
    if price > budget or price != final_offer:
        return "receipt_price_mismatch"
    if not receipt.get("sig_seller"):
        return "missing_seller_signature"
    return None


class BOriginalClient:
    """A2A v1 JSON-RPC client; cryptographic payment remains kit-v4's job."""

    def __init__(self, base_url: str, client: httpx.Client | None = None) -> None:
        self.base_url = validate_url(base_url)
        self.client = client or httpx.Client(timeout=httpx.Timeout(20, connect=8), follow_redirects=False)
        self._owns_client = client is None
        self.rpc_url: str | None = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self._owns_client:
            self.client.close()

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    @staticmethod
    def _json(response: httpx.Response) -> dict:
        try:
            response.raise_for_status()
            value = response.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            raise BuyerError("invalid HTTP/JSON response") from exc
        if not isinstance(value, dict):
            raise BuyerError("response is not an object")
        return value

    def fetch_card(self) -> None:
        card = self._json(self.client.get(self.base_url + "/.well-known/agent-card.json"))
        interfaces = card.get("supportedInterfaces", [])
        interface = next(
            (
                row
                for row in interfaces
                if isinstance(row, dict)
                and row.get("protocolBinding") == "JSONRPC"
                and str(row.get("protocolVersion")) == "1.0"
            ),
            None,
        )
        if not interface or not isinstance(interface.get("url"), str):
            raise BuyerError("no A2A v1 JSON-RPC interface")
        rpc_url = validate_url(interface["url"])
        if urlparse(rpc_url).hostname != urlparse(self.base_url).hostname:
            raise BuyerError("A2A interface is not on the shop host")
        self.rpc_url = rpc_url

    def rpc(self, method: str, params: dict) -> dict:
        if self.rpc_url is None:
            self.fetch_card()
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}
        payload = self._json(
            self.client.post(self.rpc_url, headers={"A2A-Version": "1.0"}, json=body)
        )
        if payload.get("error"):
            raise BuyerError("A2A returned an error")
        result = payload.get("result")
        task = result.get("task", result) if isinstance(result, dict) else None
        if not isinstance(task, dict):
            raise BuyerError("missing A2A task")
        return task

    def send(self, text: str, task_id: str | None = None) -> dict:
        message = {
            "role": "ROLE_USER",
            "messageId": str(uuid.uuid4()),
            "parts": [{"text": text}],
        }
        if task_id:
            message["taskId"] = task_id
        return self.rpc("SendMessage", {"message": message})

    @staticmethod
    def _ask(task: dict) -> int:
        text = " ".join(
            part.get("text", "")
            for part in task.get("status", {}).get("message", {}).get("parts", [])
            if isinstance(part, dict)
        )
        numbers = re.findall(r"\d+(?:\.\d+)?", text)
        if not numbers or not float(numbers[-1]).is_integer():
            raise BuyerError("ask is missing or non-integral")
        return int(float(numbers[-1]))

    @staticmethod
    def _receipt(task: dict) -> dict:
        for artifact in task.get("artifacts", []):
            for part in artifact.get("parts", []):
                data = part.get("data")
                if isinstance(data, dict):
                    receipt = dict(data)
                    price = receipt.get("price")
                    if isinstance(price, float) and price.is_integer():
                        receipt["price"] = int(price)
                    return receipt
        raise BuyerError("completed task has no receipt")

    def quote(self, item: str, list_price: int) -> Quote:
        self.fetch_card()
        task = self.send(f"I want to buy {item}")
        if task.get("status", {}).get("state") != INPUT_REQUIRED:
            raise BuyerError("seller did not return an input-required quote")
        return Quote(self.base_url, item, list_price, self._ask(task), task)

    def cancel(self, task_id: str | None) -> None:
        if not task_id:
            return
        try:
            self.rpc("CancelTask", {"id": task_id})
        except (BuyerError, httpx.HTTPError):
            pass

    def complete(self, quote: Quote, budget: int) -> dict:
        task = quote.task
        final_offer: int | None = None
        for turn in range(3):
            state = task.get("status", {}).get("state")
            if state == COMPLETED:
                receipt = self._receipt(task)
                problem = validate_receipt(receipt, quote.item, budget, final_offer or -1)
                if problem:
                    raise BuyerError(f"unsafe receipt: {problem}")
                return receipt
            if state != INPUT_REQUIRED:
                raise BuyerError("negotiation failed")
            task_id = task.get("id")
            if not isinstance(task_id, str) or not task_id:
                raise BuyerError("quote has no task id")
            ask = self._ask(task)
            final_offer = offer_for(ask, quote.list_price, budget, turn)
            task = self.send(str(final_offer), task_id)

        if task.get("status", {}).get("state") == COMPLETED and final_offer is not None:
            receipt = self._receipt(task)
            problem = validate_receipt(receipt, quote.item, budget, final_offer)
            if problem is None:
                return receipt
        raise BuyerError("round limit")

    def buy(self, item: str, list_price: int, budget: int) -> dict:
        return self.complete(self.quote(item, list_price), budget)


BuyerClient = BOriginalClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--budget", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        with BOriginalClient(args.url) as client:
            receipt = client.buy(
                args.item,
                list_price_from_catalog(args.catalog, args.item),
                args.budget,
            )
    except (BuyerError, httpx.HTTPError) as exc:
        print(f"buyer failed safely: {type(exc).__name__}")
        return 1
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
