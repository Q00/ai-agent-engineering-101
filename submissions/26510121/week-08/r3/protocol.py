"""Narrow A2A transport and signed receipt checks; no secrets in diagnostics."""
import base64
import json
import re
import uuid
import time
import math
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


def observe_quote(journal,task,shop,order,stage):
    """Bounded peer wire evidence for parser diagnosis, never policy input."""
    def clean(value,depth=0):
        if depth>4:return '[depth limit]'
        if isinstance(value,dict):
            return {k:clean(v,depth+1) for k,v in list(value.items())[:24]
                    if not any(s in k.lower() for s in ['sig','token','auth','key','cert'])}
        if isinstance(value,list):return [clean(v,depth+1) for v in value[:12]]
        return value[:2000] if isinstance(value,str) else value
    state=task.get('status',{})
    journal.add('buyer_quote_wire',{'order':order['order'],'shop':shop,'stage':stage,
                'task':task.get('id'),'state':state.get('state'),
                'metadata':clean(task.get('metadata',{})),
                'message':clean(state.get('message',{}))},role='buyer')


def quote(task):
    terms=quote_terms(task)
    if 'ask' in terms:return terms['ask']
    text = task_text(task)
    matches = re.findall(r"(?:\bask(?:ing)?\b|\bcounteroffer\b)\s*[:=]?\s*([\d,]+)",text,re.I)
    if not matches:
        matches=re.findall(r"(?:\bprice\b|판매가|호가|가격)\s*[:=]?\s*([\d,]+)",text,re.I)
    if not matches and re.fullmatch(r"\s*[\d,]+\s*", text):
        matches = [text.strip()]
    if len(set(matches)) != 1:
        raise ValueError("ambiguous_ask")
    return money(int(matches[0].replace(",", "")))


def quote_terms(task):
    """Explicit declarations are conditional terms, never checkout certificates."""
    message = task.get('status', {}).get('message', {})
    sources = [task.get('metadata', {}), task.get('status', {}).get('metadata', {}), message.get('metadata', {})]
    for part in message.get('parts',[]):
        data=part.get('data')
        if isinstance(data,dict):
            sources.append(data)
            sources.extend(data[k] for k in ['offer_terms','offer','quote'] if isinstance(data.get(k),dict))
    result = {}
    aliases={'asking_price':'ask','price':'ask','grade_now':'grade','claim':'grade','item':'sku'}
    for source in sources:
        if not isinstance(source, dict):
            continue
        for original in ('ask','price','asking_price','sku','item','unit','grade','grade_now','claim',
                         'expires_at','hours_left','warranty_hours','cert','certificate'):
            if original in source:
                key=aliases.get(original,original);value=source[original]
                if key=='sku' and (not isinstance(value,str) or not re.fullmatch(r'G[0-9a-z]+',value,re.I)):continue
                if key=='grade' and value not in ('A','B','C'):continue
                if key=='ask':value=money(value)
                if key in ('warranty_hours','expires_at','hours_left') and value is not None:
                    if type(value) not in (int,float) or not math.isfinite(value) or value<0:
                        raise ValueError('conflicting_offer_terms')
                    if key=='warranty_hours' and value>72:raise ValueError('conflicting_offer_terms')
                if key in ('cert','certificate') and type(value) is not bool:continue
                if key in result and result[key] != value:
                    if key=='ask':raise ValueError('conflicting_ask')
                    raise ValueError('conflicting_offer_terms')
                result[key] = value
    text=task_text(task)
    if 'grade' not in result:
        grades={g.upper() for g in re.findall(r'\b(?:grade|claim)\s*[:=]?\s*([ABC])\b',text,re.I)}
        if len(grades)==1:result['grade']=grades.pop()
    if 'sku' not in result:
        skus=set(re.findall(r'\bG[0-9a-f]{5,}\b',text,re.I))
        if len(skus)==1:result['sku']=skus.pop()
    if 'warranty_hours' not in result:
        hours=set(re.findall(r'\bwarranty_hours\s*[:=]\s*(\d+(?:\.\d+)?)',text,re.I))
        if not hours:hours=set(re.findall(r'\b(\d+(?:\.\d+)?)\s*(?:h|hours?)[ -]*warranty\b',text,re.I))
        if re.search(r'\bno warranty\b',text,re.I):hours.add('0')
        if len(hours)==1 and 0<=float(next(iter(hours)))<=72:result['warranty_hours']=float(hours.pop())
    if 'hours_left' not in result:
        hours=set(re.findall(r'\b(\d+(?:\.\d+)?)\s*(?:h|hours?)\s+(?:of\s+)?shelf life left\b',text,re.I))
        if len(hours)==1:result['hours_left']=float(hours.pop())
    result['declarations_require_checkout_verification']=True
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
