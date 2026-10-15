"""Small A2A v1.0 JSON-RPC client, keeping card/interface/continuation evidence."""
from __future__ import annotations

import uuid
import time
from urllib.parse import urlparse
import httpx


class ProtocolError(RuntimeError):
    pass


class A2AClient:
    def __init__(self, base_url, trace, timeout=120, deadline=None):
        self.base = base_url.rstrip("/")
        self.trace = trace
        self.deadline = deadline
        self.timeout = timeout
        self.http = httpx.Client(headers={"A2A-Version": "1.0"}, timeout=timeout, trust_env=False)
        try:
            response = self.http.get(self.base + "/.well-known/agent-card.json", timeout=self._remaining_timeout())
            response.raise_for_status()
            self._remaining_timeout()
            self.card = response.json()
            if not isinstance(self.card, dict) or not isinstance(self.card.get("supportedInterfaces"), list):
                raise ProtocolError("Agent Card must contain supportedInterfaces")
            interfaces = [i for i in self.card["supportedInterfaces"] if isinstance(i, dict)
                          and i.get("protocolBinding") == "JSONRPC" and str(i.get("protocolVersion")) == "1.0"]
            if not interfaces:
                raise ProtocolError("Agent Card has no JSONRPC 1.0 interface")
            self.url = interfaces[0].get("url")
            if not isinstance(self.url, str):
                raise ProtocolError("Agent Card interface URL must be text")
            # Explicitly supplied origins are trusted; card redirects cannot replace them.
            endpoint, origin = urlparse(self.url), urlparse(self.base)
            if endpoint.scheme not in ("http", "https") or (endpoint.scheme, endpoint.netloc) != (origin.scheme, origin.netloc):
                raise ProtocolError("Advertised endpoint changes the trusted shop origin")
            self.trace.write("agent_card", base_url=self.base, card=self.card)
        except Exception:
            self.http.close()
            raise

    def _remaining_timeout(self):
        remaining = self.deadline - time.monotonic() if self.deadline is not None else self.timeout
        if remaining <= 0:
            raise TimeoutError("A2A request deadline reached")
        return min(self.timeout, remaining)

    def rpc(self, method, params):
        request = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params}
        self.trace.write("rpc_request", endpoint=self.url, request=request, version="1.0")
        response = self.http.post(self.url, json=request, timeout=self._remaining_timeout())
        response.raise_for_status()
        self._remaining_timeout()
        body = response.json()
        if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
            raise ProtocolError("Expected a JSON-RPC 2.0 response object")
        if body.get("id") != request["id"]:
            raise ProtocolError("JSON-RPC response ID differs from the request")
        if ("result" in body) == ("error" in body):
            raise ProtocolError("JSON-RPC response must contain exactly one result or error")
        self.trace.write("rpc_response", response=body)
        if "error" in body:
            if not isinstance(body["error"], dict):
                raise ProtocolError("JSON-RPC error must be an object")
            raise ProtocolError(json_error(body["error"]))
        result = body["result"]
        if not isinstance(result, dict) or not isinstance(result.get("task", result), dict):
            raise ProtocolError("A2A result must be an object")
        return result.get("task", result)

    def send(self, text, task_id=None, *, stance=None):
        if stance is not None and (not isinstance(stance, str) or stance not in ("negotiable", "firm")):
            raise ProtocolError("Negotiation stance must be negotiable or firm")
        message = {"role": "ROLE_USER", "messageId": uuid.uuid4().hex, "parts": [{"text": text}]}
        if task_id:
            message["taskId"] = task_id
        if stance is not None:
            message["metadata"] = {"negotiationStance": stance}
        return self.rpc("SendMessage", {"message": message})

    def close(self):
        self.http.close()


def json_error(error):
    return f"A2A error {error.get('code')}: {error.get('message')}"


def task_text(task):
    return " ".join(p.get("text", "") for p in task.get("status", {}).get("message", {}).get("parts", []))


def task_metadata(task):
    return task.get("status", {}).get("message", {}).get("metadata", {})


def receipt_from(task):
    receipts = [p["data"] for a in task.get("artifacts", []) for p in a.get("parts", [])
                if isinstance(p.get("data"), dict) and "price" in p["data"]]
    if len(receipts) != 1:
        raise ProtocolError("Expected exactly one numeric-price receipt")
    return receipts[0]
