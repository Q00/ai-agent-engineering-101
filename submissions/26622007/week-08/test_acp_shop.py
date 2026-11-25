"""acp_shop against the kit: the kit's own code (scripts/market_kit.py, read only) is the reference.

    python -m unittest test_acp_shop -v        (from submissions/26622007/week-08)

- the ACP card is the kit's card for the same public URL,
- a session has the kit's fields, and the kit's buyer-side jwt_payload accepts our checkout_jwt,
- receipts this shop did not sign, mismatched line items, float prices and reused receipts are refused,
- complete forwards {order, checkout_jwt, checkout_mandate, payment_mandate} to /api/settle and returns
  order.mandate_receipt (what the kit's acp_pay reads), or 402 with the market's failed checks.
"""
import asyncio
import base64
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.applications import Starlette
from starlette.testclient import TestClient

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "scripts"))
import acp_shop                    # noqa: E402
import market_kit as kit           # noqa: E402

PUBLIC = "https://nocha-furniture-a2a.onrender.com"


def receipt(key, item="lamp", price=100, task="t-1", shop="Nocha Furniture"):
    r = {"shop": shop, "item": item, "price": price, "task": task}
    r["sig_seller"] = base64.b64encode(key.sign(acp_shop.canon({k: r[k] for k in acp_shop.RECEIPT_KEYS}))).decode()
    return r


class RoundTwoMarket:
    """Kit v4 asks the market for the inventory; before Round 3 there are no units."""

    def call(self, method, path, role="buyer", **kw):
        return httpx.Response(200, json={"round": 2, "units": []})


class AcpShopTest(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.settles = []
        self.market_answer = (200, {"settled": True, "ledger": 42, "mandate_receipt": {"ledger": 42, "order": "o-1"}})

        def market(request):
            self.settles.append(json.loads(request.content))
            code, body = self.market_answer
            return httpx.Response(code, json=body)

        self.logs = []
        self.acp = acp_shop.AcpCheckout(PUBLIC + "/", lambda: self.key, self.logs.append,
                                        transport=httpx.MockTransport(market))
        self.client = TestClient(Starlette(routes=self.acp.routes()))

    def create(self, rec, items=None):
        return self.client.post("/acp/checkout_sessions",
                                json={"line_items": items or [{"id": rec["item"], "quantity": 1}], "receipt": rec})

    def test_card_is_the_kits_card(self):
        mine = self.client.get("/.well-known/acp.json").json()
        self.assertEqual(mine, kit.AcpShop(self.key, None).card(PUBLIC))
        self.assertEqual(mine["endpoint"], PUBLIC + "/acp")

    def test_session_matches_the_kit_and_its_jwt_verifies(self):
        rec = receipt(self.key)
        r = self.create(rec)
        self.assertEqual(r.status_code, 201)
        mine = r.json()
        ref = json.loads(asyncio.run(kit.AcpShop(self.key, RoundTwoMarket()).create(
            {"line_items": [{"id": "lamp", "quantity": 1}], "receipt": rec}, PUBLIC)).body)
        self.assertEqual(set(mine), set(ref))
        for k in ("status", "task", "line_items", "totals", "capabilities"):
            self.assertEqual(mine[k], ref[k], k)
        co = kit.jwt_payload(mine["checkout_jwt"], kit.pub_b64(self.key))     # what the kit's buyer checks
        ref_co = kit.jwt_payload(ref["checkout_jwt"], kit.pub_b64(self.key))
        self.assertIsNotNone(co)
        self.assertEqual(set(co), set(ref_co))
        self.assertEqual((co["item"], co["price"], co["task"], co["shop_url"], co["id"]),
                         ("lamp", 100, "t-1", PUBLIC, mine["id"]))
        self.assertIsNone(kit.jwt_payload(mine["checkout_jwt"], kit.pub_b64(Ed25519PrivateKey.generate())))

    def test_refuses_what_it_should(self):
        other = Ed25519PrivateKey.generate()
        self.assertEqual(self.create(receipt(other)).status_code, 400)                      # not our signature
        forged = dict(receipt(self.key), price=90)                                          # price changed after signing
        self.assertEqual(self.create(forged).status_code, 400)
        rec = receipt(self.key, task="t-2")
        self.assertEqual(self.create(rec, items=[{"id": "chair", "quantity": 1}]).status_code, 400)
        self.assertEqual(self.create(receipt(self.key, price=100.0, task="t-3")).status_code, 400)
        self.assertEqual(self.create(rec).status_code, 201)
        self.assertEqual(self.create(rec).status_code, 409)                                 # one session per receipt
        self.assertEqual(self.client.post("/acp/checkout_sessions", content=b"[1]").status_code, 400)

    def pay(self, sid, amount=100):
        return self.client.post(f"/acp/checkout_sessions/{sid}/complete", json={"payment_data": {
            "order": "o-1", "checkout_mandate": {"mandate": {"vct": "mandate.checkout.1"}, "sig": "c"},
            "payment_mandate": {"mandate": {"vct": "mandate.payment.1", "amount": amount}, "sig": "p"}}})

    def test_complete_forwards_to_settle(self):
        s = self.create(receipt(self.key)).json()
        r = self.pay(s["id"])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["order"]["mandate_receipt"]["ledger"], 42)                # acp_pay reads this
        self.assertEqual(r.json()["status"], "completed")
        self.assertEqual(self.settles, [{"order": "o-1", "checkout_jwt": s["checkout_jwt"],
                                         "checkout_mandate": {"mandate": {"vct": "mandate.checkout.1"}, "sig": "c"},
                                         "payment_mandate": {"mandate": {"vct": "mandate.payment.1", "amount": 100},
                                                             "sig": "p"}}])
        self.assertEqual(self.pay(s["id"]).status_code, 409)                                # no second payment
        self.assertEqual(self.client.post(f"/acp/checkout_sessions/{s['id']}/cancel").status_code, 409)
        self.assertTrue(any(line.startswith("[acp] settled lamp at 100") for line in self.logs))

    def test_market_refusal_is_402(self):
        self.market_answer = (400, {"settled": False, "checks": {"round_3_order": True, "amount_in_range": False}})
        s = self.create(receipt(self.key)).json()
        r = self.pay(s["id"])
        self.assertEqual(r.status_code, 402)
        self.assertEqual(r.json()["market"]["checks"]["amount_in_range"], False)
        self.assertEqual(self.client.get(f"/acp/checkout_sessions/{s['id']}").json()["status"], "ready_for_payment")
        self.assertTrue(any("['amount_in_range']" in line for line in self.logs))

    def test_wrong_amount_never_reaches_the_market(self):
        s = self.create(receipt(self.key)).json()
        self.assertEqual(self.pay(s["id"], amount=90).status_code, 400)
        self.assertEqual(self.settles, [])

    def test_cancel(self):
        s = self.create(receipt(self.key)).json()
        self.assertEqual(self.client.post(f"/acp/checkout_sessions/{s['id']}/cancel").json()["status"], "canceled")
        self.assertEqual(self.pay(s["id"]).status_code, 409)
        self.assertEqual(self.client.get("/acp/checkout_sessions/cs_nope").status_code, 404)


class KitlogTest(unittest.TestCase):
    def setUp(self):
        os.chdir(HERE)
        import seller
        self.client = TestClient(Starlette(routes=[seller.Route("/_kitlog", seller.kitlog)]))
        self.saved = os.environ.pop("KITLOG_TOKEN", None)

    def tearDown(self):
        os.environ.pop("KITLOG_TOKEN", None)
        if self.saved is not None:
            os.environ["KITLOG_TOKEN"] = self.saved

    def test_off_without_token_and_guarded_with_one(self):
        import seller
        self.assertEqual(self.client.get("/_kitlog").status_code, 404)
        os.environ["KITLOG_TOKEN"] = "s3cret"
        self.assertEqual(self.client.get("/_kitlog").status_code, 401)
        self.assertEqual(self.client.get("/_kitlog", headers={"Authorization": "Bearer wrong"}).status_code, 401)
        with tempfile.NamedTemporaryFile("w", suffix=".log") as f, \
                mock.patch.dict(seller.KITLOGS, {"market-kit.log": f.name}):
            f.write("[kit] market_kit 4\n")
            f.flush()
            r = self.client.get("/_kitlog", headers={"Authorization": "Bearer s3cret"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("[kit] market_kit", r.text)
        self.assertEqual(self.client.get("/_kitlog?file=../agent.json",
                                         headers={"Authorization": "Bearer s3cret"}).status_code, 404)
        r = self.client.get("/_kitlog?file=seller_memory.json", headers={"Authorization": "Bearer s3cret"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("negotiations", r.json())


if __name__ == "__main__":
    unittest.main()
