"""Small A2A v1.0 JSON-RPC client; no negotiation policy lives here."""
from __future__ import annotations

import math
import uuid
from urllib.parse import urlparse

import httpx

from harness import emit


class ProtocolError(RuntimeError):
    pass


def amount(value, name="price"):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return value


def task_state(task):
    return task.get("status", {}).get("state", "")


def task_text(task):
    return " ".join(part.get("text", "") for part in
                    task.get("status", {}).get("message", {}).get("parts", []))


def receipt(task, item, budget, last_offer):
    """Check the actual artifact, never infer a purchase from natural language."""
    if task_state(task) != "TASK_STATE_COMPLETED":
        raise ValueError("receipt requires a completed task")
    candidates = [part["data"] for artifact in task.get("artifacts", [])
                  for part in artifact.get("parts", [])
                  if isinstance(part.get("data"), dict) and "price" in part["data"]]
    if len(candidates) != 1:
        raise ValueError("expected exactly one receipt with a numeric price")
    data = candidates[0]
    price = amount(data["price"])
    if item not in (data.get("item"), data.get("item_id")):
        raise ValueError(f"receipt identity does not match requested item {item!r}")
    if last_offer is None:
        raise ValueError("a receipt arrived before an authorized offer")
    if price > amount(budget, "budget") or price > amount(last_offer, "last offer"):
        raise ValueError("receipt exceeds the authorized offer or budget")
    return data


class A2AClient:
    def __init__(self, base_url, *, timeout=180, label=None):
        self.base_url = base_url.rstrip("/")
        self.endpoint = None
        self.label = label or self.base_url
        self.http = httpx.AsyncClient(
            headers={"A2A-Version": "1.0"}, timeout=timeout, trust_env=False)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.http.aclose()

    async def discover(self):
        response = await self.http.get(self.base_url + "/.well-known/agent-card.json")
        response.raise_for_status()
        card = response.json()
        interfaces = [interface for interface in card.get("supportedInterfaces", [])
                      if interface.get("protocolBinding") == "JSONRPC"
                      and str(interface.get("protocolVersion")) == "1.0"]
        if not interfaces or not card.get("skills"):
            raise ProtocolError("Agent Card has no supported A2A 1.0 JSON-RPC skills")
        endpoint = interfaces[0].get("url", "")
        if urlparse(endpoint).scheme not in ("http", "https"):
            raise ProtocolError("Agent Card endpoint is not an HTTP URL")
        self.endpoint = endpoint
        emit("card_read", shop=self.label, name=card.get("name"),
             skills=card.get("skills"), version="1.0")
        return card

    async def rpc(self, method, params):
        if self.endpoint is None:
            raise ProtocolError("read the Agent Card before sending RPC requests")
        request_id = uuid.uuid4().hex
        body = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        emit("a2a_request", shop=self.label, method=method, params=params, version="1.0")
        response = await self.http.post(self.endpoint, json=body)
        response.raise_for_status()
        payload = response.json()
        if payload.get("id") != request_id:
            raise ProtocolError("JSON-RPC response id mismatch")
        if "error" in payload:
            raise ProtocolError(f"{method}: {payload['error']}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ProtocolError("missing JSON-RPC result object")
        task = result.get("task", result)
        if not isinstance(task, dict) or "status" not in task or "id" not in task:
            raise ProtocolError("this shopping client requires an A2A Task result")
        emit("a2a_response", shop=self.label, task_id=task["id"],
             state=task_state(task), text=task_text(task),
             metadata=task.get("metadata", {}), artifacts=task.get("artifacts", []))
        return task

    async def send(self, text, task_id=None):
        message = {"role": "ROLE_USER", "messageId": uuid.uuid4().hex,
                   "parts": [{"text": text}]}
        if task_id:
            message["taskId"] = task_id
        task = await self.rpc("SendMessage", {"message": message})
        if task_id and task["id"] != task_id:
            raise ProtocolError("seller changed the task id during negotiation")
        return task

    async def get_task(self, task_id):
        return await self.rpc("GetTask", {"id": task_id})

    async def cancel(self, task_id):
        return await self.rpc("CancelTask", {"id": task_id})
