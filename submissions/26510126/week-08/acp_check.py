"""Checks a shop's ACP checkout the way kit v3's buyer (acp_pay) uses it, without a market order of our own.

    python acp_check.py https://a2a-lamp-shop.thisjonghoon.workers.dev

1. Buys a lamp over A2A at the shop's opening ask and keeps the signed receipt.
2. Reads /.well-known/acp.json and opens a checkout session with that receipt.
3. Verifies the checkout_jwt with the key the market lists for the shop and compares it with the receipt.
4. Opens the same receipt again (must be refused: one session per receipt).
5. Completes with mandates for someone else's order (must be refused by the market, which says which checks failed).
6. Cancels the session.
Nothing settles: step 5 has no buyer signature, so the market refuses it whatever the shop does.
"""
import base64, json, sys, uuid

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

MARKET = "https://market.ourolabs.ai"
NAME = "thisjonghoon"
H = {"A2A-Version": "1.0"}


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def unb64url(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def send(c, base, text, task=None):
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task:
        msg["taskId"] = task
    r = c.post(base + "/", json={"jsonrpc": "2.0", "id": "1", "method": "SendMessage", "params": {"message": msg}}, headers=H)
    return r.json()["result"]["task"]


def main(base):
    base = base.rstrip("/")
    c = httpx.Client(timeout=60)
    pub = next(s["pubkey"] for s in c.get(f"{MARKET}/api/shops").json()["shops"] if s["name"] == NAME)

    t = send(c, base, "I want to buy a lamp")
    ask = int("".join(ch for ch in t["status"]["message"]["parts"][0]["text"].split("asking")[1].split(".")[0] if ch.isdigit()))
    t = send(c, base, str(ask), t["id"])
    rec = next(p["data"] for a in t.get("artifacts", []) for p in a["parts"] if "data" in p)
    print(f"1. A2A deal: {t['status']['state']}, receipt {rec['item']} at {rec['price']}, task {rec['task'][:8]}")

    card = c.get(f"{base}/.well-known/acp.json").json()
    acp = card["endpoint"]
    r = c.post(f"{acp}/checkout_sessions", json={"line_items": [{"id": rec["item"], "quantity": 1}], "receipt": rec})
    s = r.json()
    print(f"2. acp.json endpoint {acp}; create session: {r.status_code} {s.get('id')} {s.get('status')}, total {s.get('totals')}")

    head, body, sig = s["checkout_jwt"].split(".")
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pub)).verify(unb64url(sig), f"{head}.{body}".encode())
        ok = True
    except Exception:
        ok = False
    co = json.loads(unb64url(body))
    match = (co["item"], co["price"], co["task"]) == (rec["item"], rec["price"], rec["task"])
    print(f"3. checkout_jwt signed by the market's key for {NAME}: {ok}; matches the receipt: {match}; payload {co}")

    r = c.post(f"{acp}/checkout_sessions", json={"line_items": [{"id": rec["item"], "quantity": 1}], "receipt": rec})
    print(f"4. same receipt again: {r.status_code} {r.json()}")

    led = c.get(f"{MARKET}/api/ledger", params={"after": -30}).json()
    order = [e["data"]["order"] for e in led if e["kind"] == "order"][-1]
    fake = {"mandate": {"vct": "mandate.payment.1", "order": order, "payee": base, "amount": rec["price"]}, "sig": ""}
    r = c.post(f"{acp}/checkout_sessions/{s['id']}/complete",
               json={"payment_data": {"order": order, "checkout_mandate": {"mandate": {}, "sig": ""}, "payment_mandate": fake}})
    market = r.json().get("market") or {}
    failed = [k for k, v in (market.get("checks") or {}).items() if not v]
    print(f"5. complete with someone else's order {order}: {r.status_code}; market says {market.get('settled', market)}; failed checks {failed}")

    r = c.post(f"{acp}/checkout_sessions/{s['id']}/cancel", json={})
    print(f"6. cancel: {r.status_code} {r.json().get('status')}")


if __name__ == "__main__":
    main(sys.argv[1])
