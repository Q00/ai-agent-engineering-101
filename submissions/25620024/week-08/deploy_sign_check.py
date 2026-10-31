"""Week 07 — deploy_sign_check.py: buy one item from the deployed shop and check its receipt signature.

    python deploy_sign_check.py https://harness-shop-25620024.imajason.workers.dev [lamp|chair|bookshelf]

Asks for the item, offers the shop's own opening ask (so it accepts without another model call),
and checks the receipt the way the market and the kit do: the four keys {shop, item, price, task},
task = the A2A task id, a whole-number price, and sig_seller verifying over the canonical JSON with
(1) the public key of the local shop_key.pem and (2) the public key the market has registered for
this shop's URL.
"""
import base64
import re
import sys
import uuid

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from seller import canon, load_key

MARKET = "https://market.ourolabs.ai"
HEADERS = {"A2A-Version": "1.0"}


def send(url, text, task_id=None):
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    body = {"jsonrpc": "2.0", "id": "1", "method": "SendMessage", "params": {"message": msg}}
    r = httpx.post(url, json=body, headers=HEADERS, timeout=120).json()
    if "error" in r:
        sys.exit(f"SendMessage error: {r['error']}")
    task = r["result"].get("task", r["result"])
    said = " ".join(p.get("text", "") for p in task["status"].get("message", {}).get("parts", []))
    print(f"-> {text}\n<- {task['status']['state']} | {said}")
    return task, said


def verify(pub, rec):
    body = {k: rec[k] for k in ("shop", "item", "price", "task")}
    body["price"] = int(body["price"]) if float(body["price"]).is_integer() else body["price"]
    try:
        pub.verify(base64.b64decode(rec["sig_seller"]), canon(body))
        return True
    except Exception:
        return False


def main():
    base = sys.argv[1].rstrip("/")
    item = sys.argv[2] if len(sys.argv) > 2 else "lamp"
    task, said = send(base + "/", f"I want to buy a {item}")
    ask = int(re.findall(r"\d+", said)[-1])
    task, _ = send(base + "/", str(ask), task["id"])
    rec = next(p["data"] for a in task.get("artifacts", []) for p in a["parts"] if "data" in p)
    print("receipt:", rec)

    local_pub = load_key().public_key()
    shops = httpx.get(MARKET + "/api/shops", timeout=20).json()["shops"]
    reg = next((s for s in shops if s["url"].rstrip("/") == base), None)
    checks = {
        "four keys": all(k in rec for k in ("shop", "item", "price", "task")),
        "task is the A2A task id": rec.get("task") == task["id"],
        "whole-number price": float(rec.get("price", 0.5)).is_integer(),
        "sig_seller present": "sig_seller" in rec,
        "verifies with local shop_key.pem": "sig_seller" in rec and verify(local_pub, rec),
        f"verifies with the market's key for {reg['name'] if reg else '?'}":
            bool(reg) and "sig_seller" in rec and verify(Ed25519PublicKey.from_public_bytes(base64.b64decode(reg["pubkey"])), rec),
    }
    for name, ok in checks.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
