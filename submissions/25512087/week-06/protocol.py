"""Minimal A2A v1 JSON-RPC client helpers used by the buyer."""

from __future__ import annotations

import math
import re
import uuid
from typing import Any

import httpx


class A2AError(RuntimeError):
    """Raised for transport or JSON-RPC failures."""


def build_send_message(
    text: str,
    task_id: str | None = None,
    message_id: str | None = None,
) -> dict[str, Any]:
    """Build one A2A v1 user Message."""
    message: dict[str, Any] = {
        "role": "ROLE_USER",
        "messageId": message_id or str(uuid.uuid4()),
        "parts": [{"text": text}],
    }
    if task_id:
        message["taskId"] = task_id
    return message


def rpc_request(
    text: str,
    task_id: str | None = None,
    request_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Build a SendMessage JSON-RPC request and mandatory version header."""
    payload = {
        "jsonrpc": "2.0",
        "id": request_id or str(uuid.uuid4()),
        "method": "SendMessage",
        "params": {"message": build_send_message(text, task_id=task_id)},
    }
    return payload, {"A2A-Version": "1.0"}


def task_state(task: dict[str, Any] | None) -> str:
    return (task or {}).get("status", {}).get("state", "")


def task_text(task: dict[str, Any] | None) -> str:
    parts = (task or {}).get("status", {}).get("message", {}).get("parts", [])
    return " ".join(str(part.get("text", "")) for part in parts)


def last_price(task: dict[str, Any] | None) -> int | None:
    numbers = re.findall(r"\d+(?:\.\d+)?", task_text(task))
    return math.ceil(float(numbers[-1])) if numbers else None


def receipt_price(task: dict[str, Any] | None) -> int | None:
    for artifact in (task or {}).get("artifacts", []):
        for part in artifact.get("parts", []):
            data = part.get("data")
            if isinstance(data, dict) and isinstance(data.get("price"), (int, float)):
                return math.ceil(float(data["price"]))
    return None


class A2AClient:
    """Read an Agent Card and continue one task over A2A JSON-RPC."""

    def __init__(self, base_url: str, timeout: float = 200) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.client = httpx.Client(timeout=timeout)
        self.card: dict[str, Any] | None = None
        self.rpc_url = self.base_url + "/"

    def __enter__(self) -> "A2AClient":
        self.discover()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        self.client.close()

    def discover(self) -> dict[str, Any]:
        response = self.client.get(self.base_url + "/.well-known/agent-card.json")
        response.raise_for_status()
        self.card = response.json()
        interfaces = self.card.get("supportedInterfaces") or []
        if not interfaces:
            raise A2AError("Agent Card has no supported interface")
        interface = interfaces[0]
        if interface.get("protocolBinding") != "JSONRPC":
            raise A2AError("Agent Card does not advertise JSONRPC")
        if str(interface.get("protocolVersion")) != "1.0":
            raise A2AError("Agent Card does not advertise A2A 1.0")
        self.rpc_url = str(interface.get("url") or self.rpc_url)
        return self.card

    def send(self, text: str, task_id: str | None = None) -> dict[str, Any]:
        payload, headers = rpc_request(text, task_id=task_id)
        response = self.client.post(self.rpc_url, json=payload, headers=headers)
        response.raise_for_status()
        body = response.json()
        if "error" in body:
            error = body["error"]
            raise A2AError(f"{error.get('code')}: {error.get('message', '')}")
        result = body.get("result", {})
        return result.get("task", result)
