"""A small A2A v1.0 JSON-RPC client: read the Agent Card, SendMessage, CancelTask.

Every request carries the A2A-Version: 1.0 header. Plain httpx so that what goes on
the wire is visible in one screen.
"""
import math
import re
import uuid

import httpx

HEADERS = {"A2A-Version": "1.0"}


class Shop:
    def __init__(self, base_url, timeout=240):
        self.base = base_url.rstrip("/")
        self.http = httpx.Client(headers=HEADERS, timeout=timeout)
        self.card = self.http.get(self.base + "/.well-known/agent-card.json").json()
        iface = next((i for i in self.card.get("supportedInterfaces", [])
                      if i.get("protocolBinding") == "JSONRPC" and str(i.get("protocolVersion")) == "1.0"), None)
        if iface is None:
            raise RuntimeError(f"{self.base}: no JSON-RPC 1.0 interface on the Agent Card")
        self.url = iface["url"]
        self.name = self.card.get("name", self.base)

    def rpc(self, method, params):
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
        r = self.http.post(self.url, json=body).json()
        if "error" in r:
            return {"error": r["error"]}
        res = r["result"]
        return res.get("task", res)

    def send(self, text, task_id=None):
        msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        return self.rpc("SendMessage", {"message": msg})

    def cancel(self, task_id):
        return self.rpc("CancelTask", {"id": task_id})


def state(t):
    return (t or {}).get("status", {}).get("state", "")


def text_of(t):
    return " ".join(p.get("text", "") for p in (t or {}).get("status", {}).get("message", {}).get("parts", []))


def ask_of(t):
    """The seller's current ask: metadata.ask if the seller sends it, else the last number of the text."""
    meta = (t or {}).get("status", {}).get("message", {}).get("metadata") or {}
    if isinstance(meta.get("ask"), (int, float)):
        return math.ceil(meta["ask"])
    nums = re.findall(r"\d+(?:\.\d+)?", text_of(t))
    return math.ceil(float(nums[-1])) if nums else None


def receipt(t):
    for a in (t or {}).get("artifacts", []):
        for p in a.get("parts", []):
            if isinstance(p.get("data"), dict) and "price" in p["data"]:
                return p["data"]
    return None
