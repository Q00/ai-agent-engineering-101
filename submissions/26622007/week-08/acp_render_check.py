"""Payment check against the deployed shop before Round 3 opens: which /api/settle checks does its checkout pass?

    python acp_render_check.py https://nocha-furniture-a2a.onrender.com o-8aeedd26

The order is one of my buyer's real Round 2 orders (lamp). A real settlement needs a Round 3 order whose open
mandates the market signed, so this one must be refused; the point is the market's per-check answer. The steps
are the kit's acp_pay (scripts/market_kit.py) written out, so the full answer of the market is printed:
1. the shop key here must be the key the market has for nocha12 (Render signs with the same key),
2. the deployed shop opens a checkout for a receipt it signed and returns checkout_jwt,
3. the kit's jwt_payload verifies it with the registered key (what a buyer does),
4. closed checkout and payment mandates, signed with a throwaway buyer key, go to complete; the shop
   forwards them to /api/settle and returns the market's answer.
"""
import base64
import hashlib
import json
import sys
import time

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, "../../../scripts")
import market_kit as kit                         # noqa: E402
from seller import load_shop_key, sign_receipt   # noqa: E402

shop, order_id = sys.argv[1].rstrip("/"), sys.argv[2]
key, buyer = load_shop_key(), Ed25519PrivateKey.generate()
if "--my-buyer-key" in sys.argv:                 # the key the market has for my buyer, who owns the order
    buyer = kit.load_key(kit.Path("buyer_key.pem"))
print(f"buyer key: {'buyer_key.pem' if '--my-buyer-key' in sys.argv else 'throwaway'}")
c = httpx.Client(timeout=90)

registered = next(s for s in c.get(f"{kit.MARKET_URL if hasattr(kit, 'MARKET_URL') else 'https://market.ourolabs.ai'}"
                                   "/api/shops").json()["shops"] if s["name"] == "nocha12")
print(f"1. registered nocha12: {registered['url']}, key matches shop_key.pem: {registered['pubkey'] == kit.pub_b64(key)}")

card = c.get(f"{shop}/.well-known/acp.json").json()
base = card["endpoint"]
rec = sign_receipt({"shop": "Nocha Furniture", "item": "lamp", "price": 100, "task": f"render-check-{int(time.time())}"}, key)
r = c.post(f"{base}/checkout_sessions", json={"line_items": [{"id": "lamp", "quantity": 1}], "receipt": rec})
s = r.json()
print(f"2. create: HTTP {r.status_code}, session {s.get('id')}, status {s.get('status')}, total {s.get('totals')}")

co = kit.jwt_payload(s["checkout_jwt"], registered["pubkey"])
print(f"3. checkout_jwt verified with the registered key: {co is not None} -> {co}")

h = kit.b64url(hashlib.sha256(s["checkout_jwt"].encode()).digest())
opens = {"checkout": {"vct": "mandate.checkout.open.1"}, "payment": {"vct": "mandate.payment.open.1"}}
opens = {k: hashlib.sha256(kit.canon(v)).hexdigest() for k, v in opens.items()}
cm = {"vct": "mandate.checkout.1", "order": order_id, "open_mandate": opens["checkout"],
      "checkout_jwt": s["checkout_jwt"], "checkout_hash": h}
pm = {"vct": "mandate.payment.1", "order": order_id, "open_mandate": opens["payment"], "payee": registered["url"],
      "amount": co["price"], "currency": "credits", "checkout_hash": h}
sign = lambda m: {"mandate": m, "sig": base64.b64encode(buyer.sign(kit.canon(m))).decode()}
r = c.post(f"{base}/checkout_sessions/{s['id']}/complete",
           json={"payment_data": {"order": order_id, "checkout_mandate": sign(cm), "payment_mandate": sign(pm)}})
print(f"4. complete: HTTP {r.status_code}")
print(json.dumps(r.json(), indent=1, ensure_ascii=False))
