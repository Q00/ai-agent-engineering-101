"""Check a deployed shop the way the week-07 market test checks the seller.

    python check_shop.py https://ddolcom-shop.<you>.workers.dev submissions/26620029/week-07/shop_key.pem

Runs: seller_card, seller_opens, seller_deal x3 (+ seller_signed_receipt), seller_terminal,
seller_holds_reserve x3. Needs only httpx-free stdlib + cryptography (already in the venv).
"""
import base64
import json
import math
import re
import sys
import urllib.request
import uuid

from cryptography.hazmat.primitives import serialization

RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}
DEAL = {"item": "lamp", "budget": 120, "ladder": [60, 80, 100, 110, 120]}
NO_DEAL = {"item": "bookshelf", "budget": 230, "ladder": [140, 170, 200, 230]}
MAX_MESSAGES = 8
results = []


def http(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"content-type": "application/json", "A2A-Version": "1.0",
                                          "user-agent": "check-shop/1"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def rpc(url, method, params):
    r = http("POST", url, {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params})
    if "error" in r:
        print(f"    <- error {r['error']}")
        return None, r["error"]
    t = r["result"].get("task", r["result"])
    text = " ".join(p.get("text", "") for p in t["status"].get("message", {}).get("parts", []))
    print(f"    <- {t['status']['state']} | {text}")
    return t, None


def send(url, text, tid=None):
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if tid:
        msg["taskId"] = tid
    print(f"    -> {text}")
    return rpc(url, "SendMessage", {"message": msg})


def state(t):
    return (t or {}).get("status", {}).get("state", "")


def last_number(t):
    text = " ".join(p.get("text", "") for p in (t or {}).get("status", {}).get("message", {}).get("parts", []))
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    return math.ceil(float(nums[-1])) if nums else None


def receipt_of(t):
    for a in (t or {}).get("artifacts", []):
        for p in a.get("parts", []):
            d = p.get("data")
            if isinstance(d, dict) and isinstance(d.get("price"), (int, float)):
                return d
    return None


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def whole(x):
    return int(x) if isinstance(x, float) and x.is_integer() else x


def negotiate(url, sc):
    t, _ = send(url, f"I want to buy a {sc['item']}")
    if t is None or state(t) != "TASK_STATE_INPUT_REQUIRED":
        return {"opened": False, "task": t}
    tid, opened_ask, asks, ladder = t["id"], last_number(t), [], list(sc["ladder"])
    for _ in range(MAX_MESSAGES):
        if state(t) != "TASK_STATE_INPUT_REQUIRED":
            break
        ask = last_number(t)
        if ask is not None:
            asks.append(ask)
        offer = ask if ask is not None and ask <= sc["budget"] else (ladder.pop(0) if ladder else sc["budget"])
        t, _ = send(url, str(offer), tid)
        if t is None:
            break
    return {"opened": True, "opened_ask": opened_ask, "task": t, "task_id": tid, "asks": asks}


def result(name, ok, detail):
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}")


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    base, pem = sys.argv[1].rstrip("/"), sys.argv[2]
    pub = serialization.load_pem_private_key(open(pem, "rb").read(), password=None).public_key()
    card = http("GET", base + "/.well-known/agent-card.json")
    iface = (card.get("supportedInterfaces") or [{}])[0]
    result("seller_card", bool(card.get("name") and card.get("skills") and iface.get("protocolBinding") == "JSONRPC"
                               and str(iface.get("protocolVersion")) == "1.0" and iface.get("url")),
           f"name={card.get('name')!r} url={iface.get('url')}")
    url = iface.get("url") or base + "/"
    for run in (1, 2, 3):
        print(f"\n### seller_deal #{run}")
        r = negotiate(url, DEAL)
        if run == 1:
            result("seller_opens", r["opened"] and r.get("opened_ask") is not None, f"ask {r.get('opened_ask')}")
        t = r["task"]
        rec = receipt_of(t)
        price = rec.get("price") if rec else None
        low = [a for a in r.get("asks", []) if a < RESERVE["lamp"]]
        result(f"seller_deal #{run}", state(t) == "TASK_STATE_COMPLETED" and price is not None
               and RESERVE["lamp"] <= price <= DEAL["budget"] and not low, f"{state(t)}, price {price}, asks {r.get('asks')}")
        problem = "no completed task"
        if rec:
            missing = [k for k in ("shop", "item", "price", "task", "sig_seller") if k not in rec]
            problem = f"missing {missing}" if missing else None
            if not problem and rec["task"] != t.get("id"):
                problem = "task is not the A2A task id"
            if not problem:
                try:
                    pub.verify(base64.b64decode(rec["sig_seller"]),
                               canon({k: whole(rec[k]) for k in ("shop", "item", "price", "task")}))
                except Exception:
                    problem = "sig_seller does not verify with this shop_key.pem"
        result(f"seller_signed_receipt #{run}", problem is None, problem or "sig_seller verifies")
        if run == 1 and r.get("task_id"):
            _, err = send(url, "110", r["task_id"])
            result("seller_terminal", err is not None, "error returned" if err else "no error")
    for run in (1, 2, 3):
        print(f"\n### seller_holds_reserve #{run}")
        r = negotiate(url, NO_DEAL)
        t = r["task"]
        price = (receipt_of(t) or {}).get("price")
        sold_below = state(t) == "TASK_STATE_COMPLETED" and (price is None or price < RESERVE["bookshelf"])
        low = [a for a in r.get("asks", []) if a < RESERVE["bookshelf"]]
        result(f"seller_holds_reserve #{run}", r["opened"] and not sold_below and not low,
               f"{state(t)}, asks {r.get('asks')}")
    print(f"\n{sum(results)}/{len(results)} passed -> {'PASS' if all(results) else 'FAIL'}")


if __name__ == "__main__":
    main()
