import base64
import json
import sys
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts import market_kit as kit

URL = "http://127.0.0.1:9999"
shop_key = Ed25519PrivateKey.generate()
buyer_key = Ed25519PrivateKey.generate()
shop = kit.AcpShop(shop_key, None)

class FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self.payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self.payload

class FakeHTTP:
    def __init__(self):
        self.completions = []

    def get(self, url):
        assert url == URL + "/.well-known/acp.json"
        return FakeResponse(200, shop.card(URL))

    def post(self, url, json):
        if url == URL + "/acp/checkout_sessions":
            response = shop.create(json, URL)
            return FakeResponse(
                response.status_code,
                __import__("json").loads(response.body)
            )

        if url.endswith("/complete"):
            self.completions.append(json)
            return FakeResponse(
                200, {"order": {"mandate_receipt": {"ledger": 9001}}}
            )

        raise AssertionError("Unexpected HTTP request")

    def close(self):
        pass

def simulate(price, cap, tag):
    receipt = {
        "shop": "Local Test Shop",
        "item": "lamp",
        "price": price,
        "task": tag
    }
    receipt["sig_seller"] = base64.b64encode(
        shop_key.sign(kit.canon(receipt))
    ).decode()

    order = {
        "order": tag,
        "mandates": {
            "checkout": {"mandate": {"order": tag}},
            "payment": {
                "mandate": {
                    "constraints": [
                        {"type": "payment.amount_range", "max": cap}
                    ]
                }
            }
        }
    }

    fake = FakeHTTP()
    with patch.object(kit.httpx, "Client", return_value=fake):
        result = kit.acp_pay(
            order, URL, kit.pub_b64(shop_key), receipt, buyer_key
        )

    return result, fake

result, client = simulate(121, 120, "over-cap")
assert result is None
assert len(client.completions) == 0
print("PASS: Price above mandate cap blocked")

result, client = simulate(120, 120, "within-cap")
assert result == {"ledger": 9001}
assert len(client.completions) == 1
print("PASS: Within-cap checkout reaches mocked completion")

payment_data = client.completions[0]["payment_data"]
payment = payment_data["payment_mandate"]
checkout = payment_data["checkout_mandate"]

assert payment["mandate"]["amount"] == 120
assert payment["mandate"]["payee"] == URL
assert payment["mandate"]["checkout_hash"] == checkout["mandate"]["checkout_hash"]

for signed in (payment, checkout):
    buyer_key.public_key().verify(
        base64.b64decode(signed["sig"]),
        kit.canon(signed["mandate"])
    )

print("PASS: Closed mandates signed and matched")
print("All 3 local AP2 checks passed. No real settlement occurred.")
