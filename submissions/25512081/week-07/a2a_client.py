"""A small A2A v1.0 JSON-RPC client, shared by buyer.py and the challenge-2 shopper.

Every request carries the A2A-Version: 1.0 header. The endpoint comes from the
Agent Card's JSONRPC interface, not from the base URL.
"""
import re
import uuid

import httpx

HEADERS = {"A2A-Version": "1.0", "Content-Type": "application/json"}
TERMINAL = {"TASK_STATE_COMPLETED", "TASK_STATE_REJECTED", "TASK_STATE_CANCELED",
            "TASK_STATE_FAILED"}


class A2AError(Exception):
    pass


def get_card(base_url, timeout=20):
    url = base_url.rstrip("/") + "/.well-known/agent-card.json"
    r = httpx.get(url, headers={"A2A-Version": "1.0"}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def jsonrpc_url(card, base_url):
    for itf in card.get("supportedInterfaces", []):
        if itf.get("protocolBinding") == "JSONRPC":
            return itf["url"]
    return base_url.rstrip("/") + "/"


class Peer:
    """One seller: its card and its JSON-RPC endpoint."""

    def __init__(self, base_url, timeout=200):
        self.base = base_url
        self.card = get_card(base_url)
        self.url = jsonrpc_url(self.card, base_url)
        self.http = httpx.Client(timeout=timeout)

    def _rpc(self, method, params):
        body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex[:8], "method": method, "params": params}
        r = self.http.post(self.url, json=body, headers=HEADERS).json()
        if "error" in r:
            raise A2AError(f"{r['error'].get('code')} {r['error'].get('message', '')[:160]}")
        res = r["result"]
        return res.get("task", res)

    def send(self, text, task_id=None):
        msg = {"role": "ROLE_USER", "messageId": uuid.uuid4().hex, "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        return Reply(self._rpc("SendMessage", {"message": msg}))

    def cancel(self, task_id):
        return Reply(self._rpc("CancelTask", {"id": task_id}))


class Reply:
    """The parts of a task the buyer needs."""

    def __init__(self, task):
        self.task = task
        st = task.get("status", {})
        self.id = task.get("id")
        self.state = st.get("state", "")
        msg = st.get("message", {}) or {}
        self.text = " ".join(p.get("text", "") for p in msg.get("parts", []))
        meta = msg.get("metadata") or {}
        nums = re.findall(r"\d+(?:\.\d+)?", self.text)
        # the seller's current ask: metadata {"ask": N} if present, else the last number
        self.ask = meta.get("ask") if isinstance(meta.get("ask"), (int, float)) else (
            int(float(nums[-1])) if nums else None)
        self.price = None
        for a in task.get("artifacts", []):
            for p in a.get("parts", []):
                d = p.get("data")
                if isinstance(d, dict) and isinstance(d.get("price"), (int, float)):
                    self.price = d["price"]
                    self.receipt = d

    @property
    def done(self):
        return self.state in TERMINAL
