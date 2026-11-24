"""B-original deterministic negotiation policy for the existing A2A BuyerClient transport."""
from __future__ import annotations

import json
import argparse
import re
import uuid
from urllib.parse import urlparse

import httpx


def list_price_from_catalog(path, sku: str) -> int:
    from pathlib import Path
    try:
        catalog = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CandidateBuyerError("catalog is unavailable") from exc
    product = catalog.get(sku) if isinstance(catalog, dict) else None
    price = product.get("list_price") if isinstance(product, dict) else None
    if type(price) is not int or price <= 0:
        raise CandidateBuyerError("catalog has no valid list price")
    return price


def offer_for(ask: int, list_price: int, budget: int, turn: int, max_turns: int = 3) -> int:
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (ask, list_price, budget, turn)):
        raise ValueError("prices and turn must be integers")
    if budget <= 0 or max_turns <= 0 or not 0 <= turn < max_turns:
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


class CandidateBuyerError(RuntimeError):
    pass


class BOriginalClient:
    """Small A2A v1 JSON-RPC client; payment remains the kit's responsibility."""
    def __init__(self, base_url: str, client: httpx.Client) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise CandidateBuyerError("invalid seller URL")
        self.base_url, self.client, self.rpc_url = base_url.rstrip("/"), client, None

    def _json(self, response: httpx.Response) -> dict:
        try:
            response.raise_for_status(); value = response.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            raise CandidateBuyerError("invalid HTTP/JSON") from exc
        if not isinstance(value, dict):
            raise CandidateBuyerError("response is not an object")
        return value

    def fetch_card(self) -> None:
        card = self._json(self.client.get(self.base_url + "/.well-known/agent-card.json"))
        interfaces = card.get("supportedInterfaces", [])
        interface = next((row for row in interfaces if row.get("protocolBinding") == "JSONRPC"
                          and str(row.get("protocolVersion")) == "1.0"), None)
        if not interface:
            raise CandidateBuyerError("no A2A v1 JSON-RPC interface")
        self.rpc_url = interface["url"]

    def send(self, text: str, task_id: str | None = None) -> dict:
        message = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
        if task_id:
            message["taskId"] = task_id
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "SendMessage",
                "params": {"message": message}}
        payload = self._json(self.client.post(self.rpc_url, headers={"A2A-Version": "1.0"}, json=body))
        task = payload.get("result", {}).get("task", payload.get("result"))
        if not isinstance(task, dict):
            raise CandidateBuyerError("missing A2A task")
        return task

    def buy(self, item: str, list_price: int, budget: int) -> dict:
        self.fetch_card(); task = self.send(f"I want {item}")
        final_offer = None
        for turn in range(3):
            state = task.get("status", {}).get("state")
            if state == "TASK_STATE_COMPLETED":
                receipt = next((part.get("data") for art in task.get("artifacts", [])
                                for part in art.get("parts", []) if isinstance(part.get("data"), dict)), None)
                if receipt and isinstance(receipt.get("price"), float) and receipt["price"].is_integer():
                    receipt = {**receipt, "price": int(receipt["price"])}
                problem = validate_receipt(receipt, item, budget, final_offer) if receipt else "missing_receipt"
                if problem is not None:
                    raise CandidateBuyerError(f"unsafe receipt: {problem}")
                return receipt
            if state != "TASK_STATE_INPUT_REQUIRED":
                raise CandidateBuyerError("negotiation failed")
            text = " ".join(p.get("text", "") for p in task.get("status", {}).get("message", {}).get("parts", []))
            numbers = re.findall(r"\d+", text)
            if not numbers:
                raise CandidateBuyerError("ask is missing")
            final_offer = offer_for(int(numbers[-1]), list_price, budget, turn)
            task = self.send(str(final_offer), task.get("id"))
        if task.get("status", {}).get("state") == "TASK_STATE_COMPLETED":
            receipt = next((part.get("data") for art in task.get("artifacts", [])
                            for part in art.get("parts", []) if isinstance(part.get("data"), dict)), None)
            if receipt and validate_receipt(receipt, item, budget, final_offer) is None:
                return receipt
        raise CandidateBuyerError("round limit")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True); parser.add_argument("--item", required=True)
    parser.add_argument("--catalog", required=True); parser.add_argument("--budget", type=int, required=True)
    args = parser.parse_args()
    with httpx.Client(timeout=20) as client:
        print(json.dumps(BOriginalClient(args.url, client).buy(
                         args.item, list_price_from_catalog(args.catalog, args.item), args.budget),
                         ensure_ascii=False, sort_keys=True))
