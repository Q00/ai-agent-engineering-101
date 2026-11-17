"""Week 08 — deploy_acp_check.py: check the deployed shop's own ACP checkout before Round 3 opens.

    python deploy_acp_check.py https://harness-shop-25620024.imajason.workers.dev

1. reads /.well-known/acp.json and checks the endpoint is on the shop's host
2. buys a lamp over A2A (deploy_sign_check.send) to get a signed receipt
3. opens a checkout session with that receipt; a second one for the same receipt and one with a changed
   price must be refused
4. verifies the checkout_jwt with the shop's key as the market has it registered (kit v3's jwt_payload)
5. completes the session with mandates signed by a throwaway key for a Round 2 order. The market must
   refuse it; this shows the shop forwards to /api/settle and which of the market's checks pass
   (checkout_signed_by_shop should be true, the round and buyer checks false).
"""
import base64
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import market_kit as kit                                  # noqa: E402  (the course kit, unchanged)

from deploy_sign_check import MARKET, send                # noqa: E402


def main():
    base = sys.argv[1].rstrip("/")
    c = httpx.Client(timeout=60)
    card = c.get(base + "/.well-known/acp.json").json()
    print("acp.json:", card["protocol"], card["version"], card["endpoint"], [t["name"] for t in card["tools"]])
    acp = card["endpoint"].rstrip("/")
    assert urlparse(acp).hostname == urlparse(base).hostname, "endpoint not on the shop's host"

    task, said = send(base + "/", "I want to buy a lamp")
    ask = int(re.findall(r"\d+", said)[-1])
    task, _ = send(base + "/", str(ask), task["id"])
    rec = next(p["data"] for a in task.get("artifacts", []) for p in a["parts"] if "data" in p)
    rec["price"] = int(rec["price"])
    print("receipt:", {k: rec[k] for k in ("shop", "item", "price", "task")})

    r = c.post(acp + "/checkout_sessions", json={"line_items": [{"id": rec["item"], "quantity": 1}], "receipt": rec})
    print("create:", r.status_code)
    s = r.json()
    print("  again:", c.post(acp + "/checkout_sessions", json={"line_items": [{"id": rec["item"], "quantity": 1}],
                                                             "receipt": rec}).status_code, "(409 expected)")
    print("  forged price:", c.post(acp + "/checkout_sessions", json={"line_items": [{"id": rec["item"], "quantity": 1}],
                                                                    "receipt": dict(rec, price=rec["price"] - 5)}).status_code,
          "(400 expected)")
    print("  get:", c.get(f"{acp}/checkout_sessions/{s['id']}").json()["status"])

    reg = next(x for x in c.get(MARKET + "/api/shops").json()["shops"] if x["url"].rstrip("/") == base)
    co = kit.jwt_payload(s["checkout_jwt"], reg["pubkey"])
    print("checkout_jwt verifies with the market's key for", reg["name"], ":", co is not None, co)

    order = next(e["data"]["order"] for e in reversed(c.get(MARKET + "/api/ledger?after=-50").json())
                 if e.get("kind") == "order")
    fake = Ed25519PrivateKey.generate()
    h = kit.b64url(hashlib.sha256(s["checkout_jwt"].encode()).digest())
    cm = {"vct": "mandate.checkout.1", "order": order, "open_mandate": "0" * 64, "checkout_jwt": s["checkout_jwt"],
          "checkout_hash": h}
    pm = {"vct": "mandate.payment.1", "order": order, "open_mandate": "0" * 64, "payee": base, "amount": rec["price"],
          "currency": "credits", "checkout_hash": h}
    sign = lambda m: {"mandate": m, "sig": base64.b64encode(fake.sign(kit.canon(m))).decode()}
    r = c.post(f"{acp}/checkout_sessions/{s['id']}/complete",
               json={"payment_data": {"order": order, "checkout_mandate": sign(cm), "payment_mandate": sign(pm)}})
    body = r.json()
    print(f"complete with a Round 2 order and a throwaway buyer key: {r.status_code} (402 expected)")
    print(json.dumps((body.get("market") or body).get("checks", body), indent=1))
    print("  cancel after refusal:", c.post(f"{acp}/checkout_sessions/{s['id']}/cancel").json()["status"])


if __name__ == "__main__":
    main()
