"""Narrow A2A transport and signed receipt checks; no secrets in diagnostics."""
import base64
import json
import re
import uuid
import time
from urllib.parse import urljoin, urlsplit
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

FIELDS = ("shop", "item", "price", "task")


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def money(value):
    if type(value) in (int, float) and value > 0 and float(value).is_integer():
        return int(value)
    raise ValueError("invalid_money")


def signed_body(rec):
    value = {k: rec[k] for k in FIELDS}
    value["price"] = money(value["price"])
    return value


def verify_receipt(rec, pub, *, task, sku, budget, agreed):
    try:
        body = signed_body(rec)
        if body["task"] != task or body["item"] != sku or body["price"] > budget or body["price"] != agreed:
            return False
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pub, validate=True)).verify(
            base64.b64decode(rec["sig_seller"], validate=True), canon(body))
        return True
    except Exception:
        return False


def task_text(task):
    return " ".join(p.get("text", "") for p in task.get("status", {}).get("message", {}).get("parts", []))


def quote(task):
    holders = (task, task.get("status", {}).get("message", {}))
    values = [money(h["metadata"]["ask"]) for h in holders if "ask" in h.get("metadata", {})]
    if values:
        if len(set(values)) != 1:
            raise ValueError("conflicting_ask")
        return values[0]
    text = task_text(task)
    matches = re.findall(r"(?:asking|counteroffer|price|호가|가격)\s*[:=]?\s*([\d,]+)", text, re.I)
    if not matches and re.fullmatch(r"\s*[\d,]+\s*", text):
        matches = [text.strip()]
    if len(set(matches)) != 1:
        raise ValueError("ambiguous_ask")
    return money(int(matches[0].replace(",", "")))


def quote_terms(task):
    """Read explicit structured terms; never infer warranty/grade from prose."""
    message = task.get('status', {}).get('message', {})
    sources = [task.get('metadata', {}), task.get('status', {}).get('metadata', {}), message.get('metadata', {})]
    sources += [p['data']['offer_terms'] for p in message.get('parts', [])
                if isinstance(p.get('data'), dict) and isinstance(p['data'].get('offer_terms'), dict)]
    result = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in ('ask', 'sku', 'grade', 'expires_at', 'warranty_hours', 'cert', 'certificate'):
            if key in source:
                if key in result and result[key] != source[key]:
                    raise ValueError('conflicting_offer_terms')
                result[key] = source[key]
    return result


class Peer:
    def __init__(self, transport=None, timeout=8):
        self.http = httpx.Client(transport=transport, timeout=timeout, trust_env=False, follow_redirects=False,
                                 headers={"A2A-Version": "1.0"})
        self.endpoint = None
        self.deadline = None

    def budget(self):
        if self.deadline is None:return {}
        left=self.deadline-time.time()
        if left<=0:raise TimeoutError('buyer_deadline')
        return {'timeout':min(8,left)}

    def discover(self, base):
        base = base.rstrip("/")
        parts = urlsplit(base)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.username or parts.password:
            raise ValueError("invalid_peer_url")
        r = self.http.get(base + "/.well-known/agent-card.json",**self.budget())
        r.raise_for_status()
        card = r.json()
        iface = next((i for i in card.get("supportedInterfaces", [])
                      if i.get("protocolBinding") == "JSONRPC" and i.get("protocolVersion") == "1.0"), None)
        if not iface:
            raise ValueError("unsupported_a2a")
        endpoint = urljoin(base + "/", iface["url"])
        if urlsplit(endpoint).netloc != parts.netloc:
            raise ValueError("cross_host_a2a")
        self.endpoint = endpoint
        return card

    def send(self, text, task=None):
        message = {"role": "ROLE_USER", "messageId": uuid.uuid4().hex, "parts": [{"text": text}]}
        if task:
            message["taskId"] = task
        r = self.http.post(self.endpoint, json={"jsonrpc": "2.0", "id": uuid.uuid4().hex,
                                               "method": "SendMessage", "params": {"message": message}},**self.budget())
        r.raise_for_status()
        payload = r.json()
        if "error" in payload:
            raise ValueError("peer_rpc_error")
        result = payload.get("result", {})
        result = result.get("task", result)
        if not result.get("id") or not result.get("status"):
            raise ValueError("task_required")
        return result

    def close(self):
        self.http.close()

    def cancel(self, task):
        # Best effort: not every course peer implements A2A CancelTask.
        self.http.post(self.endpoint, json={'jsonrpc': '2.0', 'id': uuid.uuid4().hex,
                       'method': 'CancelTask', 'params': {'id': task}}, timeout=2)
