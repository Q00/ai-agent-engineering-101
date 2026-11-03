"""Minimal A2A v1.0 JSON-RPC client for the buyer (SendMessage / CancelTask)."""
import uuid
from urllib.parse import urlparse

import httpx


class A2AError(RuntimeError):
    pass


class Client:
    def __init__(self, base, timeout=60):
        self.base = base.rstrip("/")
        self.http = httpx.Client(headers={"A2A-Version": "1.0"}, timeout=timeout, trust_env=False,
                                 follow_redirects=False)
        r = self.http.get(self.base + "/.well-known/agent-card.json")
        r.raise_for_status()
        self.card = r.json()
        iface = next((i for i in self.card.get("supportedInterfaces") or []
                      if i.get("protocolBinding") == "JSONRPC"), None)
        url = (iface or {}).get("url") or self.base
        a, b = urlparse(url), urlparse(self.base)
        # the card may not send us to another host: the shop we chose is the shop we talk to
        self.url = url if (a.scheme, a.netloc) == (b.scheme, b.netloc) else self.base

    def rpc(self, method, params):
        body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex[:8], "method": method, "params": params}
        r = self.http.post(self.url, json=body)
        j = r.json()
        if "error" in j:
            raise A2AError(str(j["error"])[:300])
        res = j.get("result") or {}
        return res.get("task", res)

    def send(self, text, task_id=None):
        msg = {"role": "ROLE_USER", "messageId": uuid.uuid4().hex, "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        return self.rpc("SendMessage", {"message": msg})

    def cancel(self, task_id):
        try:
            self.rpc("CancelTask", {"id": task_id})
        except Exception:
            pass

    def close(self):
        self.http.close()


def state(task):
    return (task.get("status") or {}).get("state")


def said(task):
    msg = (task.get("status") or {}).get("message") or {}
    return " ".join(p.get("text", "") for p in msg.get("parts") or [] if isinstance(p, dict))


def receipt(task):
    for art in task.get("artifacts") or []:
        for part in art.get("parts") or []:
            d = part.get("data")
            if isinstance(d, dict) and d.get("sig_seller"):
                return d
    return None
