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
    """The shop's ask: the last plain number in its message, rounded up; None if absent or ambiguous
    ("1,000" or "185.000" is refused rather than misread, and the buyer then walks)."""
    from negotiation import parse_price
    value, problem = parse_price(task_text(task), round_up=True)
    return None if problem else value


def receipt_price(task: dict[str, Any] | None) -> int | None:
    receipt = receipt_data(task)
    if receipt is not None and isinstance(receipt.get("price"), (int, float)):
        return math.ceil(float(receipt["price"]))
    return None


def receipt_data(task: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the receipt data Part exactly as received from the shop."""
    for artifact in (task or {}).get("artifacts", []):
        for part in artifact.get("parts", []):
            data = part.get("data")
            if isinstance(data, dict) and isinstance(data.get("price"), (int, float)):
                return dict(data)
    return None


MAX_RESPONSE_BYTES = 1_000_000
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def same_shop(base_url: str, rpc_url: str) -> bool:
    """May the buyer follow the card's interface URL? Blocks the dangerous cases only (SSRF):
    a non-http scheme, or a private / loopback / link-local / internal address reached from a public shop.
    The same host, or another public https host with a name (a shop may serve its API elsewhere), is allowed."""
    import ipaddress
    from urllib.parse import urlsplit
    base, rpc = urlsplit(base_url), urlsplit(rpc_url)
    host = (rpc.hostname or "").lower().rstrip(".")
    base_host = (base.hostname or "").lower().rstrip(".")
    if rpc.scheme not in ("http", "https") or not host:
        return False
    if host == base_host:
        return True
    if base_host in LOOPBACK:                                   # the kit's local proxy and the test shops
        return host in LOOPBACK
    if host in LOOPBACK or host.endswith((".local", ".internal", ".localhost", ".lan", ".home.arpa")):
        return False
    if re.fullmatch(r"[0-9.]+|0x[0-9a-f.x]+|[0-9a-f:]+", host):  # any IP-literal spelling (127.1, 0x7f000001, ::1)
        try:
            ip = ipaddress.ip_address(host)
            if not ip.is_global:                               # private, loopback, link-local, 100.64/10, ...
                return False
        except ValueError:
            return False                                       # odd numeric forms that resolvers still accept
    return rpc.scheme == "https"


def read_json(response: httpx.Response) -> Any:
    """Parse a response body read with a size cap, so a hostile shop cannot exhaust memory."""
    data = b""
    for chunk in response.iter_bytes():
        data += chunk
        if len(data) > MAX_RESPONSE_BYTES:
            raise A2AError(f"response larger than {MAX_RESPONSE_BYTES} bytes")
    import json as _json
    return _json.loads(data)


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
        with self.client.stream("GET", self.base_url + "/.well-known/agent-card.json",
                                headers={"Accept-Encoding": "identity"}) as response:
            response.raise_for_status()
            self.card = read_json(response)
        interfaces = self.card.get("supportedInterfaces") or []
        if not interfaces:
            raise A2AError("Agent Card has no supported interface")
        interface = interfaces[0]
        if interface.get("protocolBinding") != "JSONRPC":
            raise A2AError("Agent Card does not advertise JSONRPC")
        if str(interface.get("protocolVersion")) != "1.0":
            raise A2AError("Agent Card does not advertise A2A 1.0")
        rpc_url = str(interface.get("url") or self.rpc_url)
        if not same_shop(self.base_url, rpc_url):
            raise A2AError(f"Agent Card points to another host ({rpc_url[:80]}); not following it")
        self.rpc_url = rpc_url
        return self.card

    def send(self, text: str, task_id: str | None = None) -> dict[str, Any]:
        payload, headers = rpc_request(text, task_id=task_id)
        headers = {**headers, "Accept-Encoding": "identity"}   # the size cap counts what was sent, not a zip bomb
        with self.client.stream("POST", self.rpc_url, json=payload, headers=headers) as response:
            response.raise_for_status()
            body = read_json(response)
        if not isinstance(body, dict):
            raise A2AError("JSON-RPC response is not an object")
        if "error" in body:
            error = body["error"]
            raise A2AError(f"{error.get('code')}: {error.get('message', '')}")
        result = body.get("result", {})
        return result.get("task", result)
