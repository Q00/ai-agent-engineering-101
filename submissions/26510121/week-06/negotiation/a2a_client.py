"""A2A transport and receipt validation; no offer-selection strategy."""
from decimal import Decimal, InvalidOperation
import math
import re
from urllib.parse import urljoin, urlsplit
import uuid

import httpx


class ProtocolError(RuntimeError):
    pass


def integer_money(value):
    if type(value) is int and value > 0:
        return value
    if type(value) is float and math.isfinite(value) and value > 0 and value.is_integer():
        return int(value)
    raise ProtocolError("invalid_numeric_price")


def task_text(task):
    return " ".join(part.get("text", "") for part in task.get("status", {}).get("message", {}).get("parts", []))


def asking_price(task):
    # Baseline contract only. The long-message challenge must replace this rule.
    matches = re.findall(r"[+-]?\d+(?:\.\d+)?", task_text(task))
    if not matches:
        raise ProtocolError("asking_price_missing")
    try:
        amount = Decimal(matches[-1])
        if not amount.is_finite() or amount <= 0 or amount != amount.to_integral_value():
            raise ProtocolError("invalid_numeric_price")
        return int(amount)
    except (InvalidOperation, ValueError):
        raise ProtocolError("invalid_numeric_price") from None


def receipt(task, item, budget, agreed_price):
    if task.get("status", {}).get("state") != "TASK_STATE_COMPLETED":
        raise ProtocolError("receipt_requires_completed_task")
    candidates = [part["data"] for artifact in task.get("artifacts", [])
                  for part in artifact.get("parts", [])
                  if isinstance(part.get("data"), dict) and "price" in part["data"]]
    if len(candidates) != 1:
        raise ProtocolError("one_receipt_required")
    data = candidates[0]
    price = integer_money(data["price"])
    if data.get("item") != item:
        raise ProtocolError("receipt_item_mismatch")
    if price > budget:
        raise ProtocolError("receipt_above_budget")
    if agreed_price is None or price != agreed_price:
        raise ProtocolError("receipt_price_mismatch")
    return {**data, "price": price}


def _http_url(url):
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username or parts.password:
        raise ProtocolError("invalid_agent_url")
    return url


class A2AClient:
    def __init__(self, log, transport=None, *, timeout=10):
        self.log = log
        self.endpoint = None
        self.http = httpx.AsyncClient(headers={"A2A-Version": "1.0"}, timeout=timeout,
                                      trust_env=False, follow_redirects=False, transport=transport)

    async def close(self):
        await self.http.aclose()

    async def discover(self, base_url):
        base_url = _http_url(base_url).rstrip("/")
        response = await self.http.get(base_url + "/.well-known/agent-card.json")
        response.raise_for_status()
        card = response.json()
        if not card.get("name") or not card.get("skills"):
            raise ProtocolError("invalid_agent_card")
        interfaces = [i for i in card.get("supportedInterfaces", [])
                      if i.get("protocolBinding") == "JSONRPC" and str(i.get("protocolVersion")) == "1.0"]
        if not interfaces or not interfaces[0].get("url"):
            raise ProtocolError("jsonrpc_v1_interface_missing")
        self.endpoint = _http_url(urljoin(base_url + "/", interfaces[0]["url"]))
        self.log.record("agent_card", card=card, endpoint=self.endpoint)
        return card

    async def rpc(self, method, params):
        if self.endpoint is None:
            raise ProtocolError("agent_card_not_resolved")
        body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params}
        self.log.record("a2a_request", method=method, params=params)
        response = await self.http.post(self.endpoint, json=body)
        response.raise_for_status()
        payload = response.json()
        if "error" in payload:
            self.log.record("a2a_error", code=payload["error"].get("code"))
            raise ProtocolError("a2a_rpc_error")
        result = payload.get("result", {})
        task = result.get("task", result)
        if not isinstance(task, dict) or not task.get("id") or not isinstance(task.get("status"), dict):
            raise ProtocolError("task_response_required")
        self.log.record("a2a_task", task=task)
        return task

    async def send(self, text, task_id=None):
        message = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
        if task_id is not None:
            message["taskId"] = task_id
        return await self.rpc("SendMessage", {"message": message})

    async def get_task(self, task_id):
        return await self.rpc("GetTask", {"id": task_id})

    async def cancel(self, task_id):
        return await self.rpc("CancelTask", {"id": task_id})
