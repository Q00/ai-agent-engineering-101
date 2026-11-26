"""The deployed shop's checkout against the real market, before Round 3 opens.

A real settlement needs a Round 3 order, whose open mandates only the market
can sign. Everything else can be judged by the market already: this script
buys a lamp from the deployed shop over A2A, opens a checkout with that
receipt, signs the closed mandates with the buyer key for one of this buyer's
own Round 2 lamp orders, and completes. The shop forwards it to /api/settle
under its shop token, and the market answers with its checks. Expected: the
round and the open-mandate checks fail (there are no open mandates in Round 2);
the shop's checkout_jwt signature, the buyer's signatures, the binding to this
checkout, the payee and the amount pass.

    python acp_live_check.py        (writes logs/acp-live-check.log)
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
import uuid
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import serialization

import acp

SHOP = "https://kiro-shop.kiro729.workers.dev"
MARKET = "https://market.ourolabs.ai"
HERE = Path(__file__).resolve().parent
LOG = HERE / "logs" / "acp-live-check.log"
lines: list[str] = []


def say(line: str) -> None:
    print(line, flush=True)
    lines.append(line)


def key(name: str):
    return serialization.load_pem_private_key((HERE / name).read_bytes(), password=None)


def a2a(c: httpx.Client, text: str, task_id: str | None = None) -> dict:
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    r = c.post(SHOP + "/", headers={"A2A-Version": "1.0"},
               json={"jsonrpc": "2.0", "id": "1", "method": "SendMessage", "params": {"message": msg}})
    task = r.json()["result"]["task"]
    say(f"[buyer] -> {text!r}  <- {task['status']['state']} | {task['status']['message']['parts'][0]['text'][:90]}")
    return task


def main() -> int:
    shop_key, buyer_key = key("shop_key.pem"), key("buyer_key.pem")
    c = httpx.Client(timeout=60)

    say("== 1. a real deal with the deployed shop over A2A")
    t = a2a(c, "I want to buy a lamp")
    ask = (t["status"]["message"].get("metadata") or {}).get("ask")
    t = a2a(c, str(ask), t["id"])
    rec = next(p["data"] for a in t.get("artifacts") or [] for p in a["parts"] if "data" in p)
    rec["price"] = int(rec["price"])
    say(f"receipt: {json.dumps(rec)}")
    say(f"receipt signed by shop_key.pem: {acp.verify_receipt(acp.pub_b64(shop_key), rec)}")

    say("\n== 2. the checkout")
    card = c.get(SHOP + "/.well-known/acp.json").json()
    base = card["endpoint"]
    say(f"acp.json endpoint {base}, services {card['capabilities']['services']}")
    r = c.post(base + "/checkout_sessions", json={"line_items": [{"id": rec["item"], "quantity": 1}], "receipt": rec})
    s = r.json()
    say(f"create -> {r.status_code}, session {s.get('id')}, status {s.get('status')}, totals {s.get('totals')}")
    co = acp.jwt_payload(s["checkout_jwt"], acp.pub_b64(shop_key))
    say(f"checkout_jwt payload (verified with shop_key.pem): {json.dumps(co)}")
    r2 = c.post(base + "/checkout_sessions", json={"line_items": [{"id": rec["item"], "quantity": 1}], "receipt": rec})
    say(f"same receipt again -> {r2.status_code} {r2.json().get('error')}")

    say("\n== 3. complete with closed mandates for one of this buyer's own Round 2 lamp orders")
    ledger = c.get(MARKET + "/api/ledger", params={"after": -2000}).json()
    order = next(e["data"]["order"] for e in reversed(ledger)
                 if e["kind"] == "order" and e["data"].get("buyer") == "Kiro729" and e["data"].get("item") == rec["item"])
    say(f"order {order} (round 2, so it has no open mandates)")
    h = acp.b64url(hashlib.sha256(s["checkout_jwt"].encode()).digest())
    cm = {"vct": "mandate.checkout.1", "order": order, "open_mandate": None, "checkout_jwt": s["checkout_jwt"],
          "checkout_hash": h}
    pm = {"vct": "mandate.payment.1", "order": order, "open_mandate": None, "payee": SHOP, "amount": co["price"],
          "currency": "credits", "checkout_hash": h}
    sign = lambda m: {"mandate": m, "sig": base64.b64encode(buyer_key.sign(acp.canon(m))).decode()}  # noqa: E731
    r = c.post(f"{base}/checkout_sessions/{s['id']}/complete",
               json={"payment_data": {"order": order, "checkout_mandate": sign(cm), "payment_mandate": sign(pm)}})
    out = r.json()
    say(f"complete -> {r.status_code} {out.get('error')}")
    checks = ((out.get("market") or {}).get("checks")) or {}
    for k, v in checks.items():
        say(f"  {'PASS' if v else 'fail'}  {k}")
    if not checks:
        say(f"  market said: {json.dumps(out)[:400]}")

    say("\n== 4. the shop's settlement log")
    say(json.dumps({k: v for k, v in c.get(SHOP + "/shop/log", params={"n": 1}).json().items()}, indent=1)[:800])
    LOG.parent.mkdir(exist_ok=True)
    LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    must = ("checkout_signed_by_shop", "checkout_mandate_signed_by_buyer", "payment_mandate_signed_by_buyer",
            "closed_mandates_types", "bound_to_this_checkout", "payee_is_this_shop", "amount_is_checkout_total")
    return 0 if checks and all(checks.get(k) for k in must) else 1


if __name__ == "__main__":
    sys.exit(main())
