"""End-to-end check of the shop's ACP checkout with the kit's own buyer side (scripts/market_kit.py acp_pay).

    python seller.py --port 9311 &                  (from submissions/26622007/week-08; uses shop_key.pem)
    python acp_local_check.py http://127.0.0.1:9311

The buyer reads /.well-known/acp.json, opens a checkout with a receipt this shop signed, checks the signed
checkout_jwt against the receipt and the mandate limit, signs closed AP2 mandates with a throwaway buyer key and
completes. The order is made up, so the real market's /api/settle refuses it: reaching that refusal shows every
step up to the market works. A second run with the limit below the price must stop before paying.
"""
import base64
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, "../../../scripts")
import market_kit as kit                 # noqa: E402
from seller import load_shop_key, sign_receipt   # noqa: E402

shop = sys.argv[1].rstrip("/")
key = load_shop_key()
buyer = Ed25519PrivateKey.generate()


def order(limit, n):
    return {"order": f"o-localcheck{n}", "round": 3, "item": "lamp", "mandates": {
        "checkout": {"mandate": {"vct": "mandate.checkout.open.1", "checkout.line_items": [{"id": "lamp"}]}},
        "payment": {"mandate": {"vct": "mandate.payment.open.1",
                                "constraints": [{"type": "payment.amount_range", "min": 0, "max": limit}]}}}}


for n, limit in ((1, 120), (2, 90)):
    rec = sign_receipt({"shop": "Nocha Furniture", "item": "lamp", "price": 100, "task": f"local-check-{n}"}, key)
    kit.log(f"--- run {n}: lamp at 100, mandate limit {limit}")
    res = kit.acp_pay(order(limit, n), shop, kit.pub_b64(key), rec, buyer)
    kit.log(f"--- run {n}: acp_pay returned {res}")
