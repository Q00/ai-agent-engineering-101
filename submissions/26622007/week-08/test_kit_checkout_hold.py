"""Kit checkout: a session nobody pays stops holding its unit. The seller puts an unpaid unit on sale again after
shop_stock.PAY_SECONDS; if the kit still held it, every new deal on it was refused "not in stock"
(11/07 04:00-05:30: 21 of 41 refusals, u-ccaf8fc3c18c and u-9031b46cfc88)."""
import asyncio
import json
import os
import sys
import threading
import unittest
from unittest import mock

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "scripts"))
import market_kit as kit           # noqa: E402
import shop_stock                  # noqa: E402
from test_acp_shop import receipt  # noqa: E402

UNIT = {"unit": "u-1", "sku": "G1", "grade": "A", "cert": "c", "category": "x>lamp", "expires_at": None,
        "supplied_at": 1}


class OneUnitMarket:
    def call(self, method, path, role="buyer", **kw):
        return httpx.Response(200, json={"round": 3, "units": [UNIT]})


class CheckoutHoldTest(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.shop = kit.AcpShop(self.key, OneUnitMarket())
        self.shop.terms["t-1"] = {"unit": "u-1"}
        self.shop.terms["t-2"] = {"unit": "u-1"}

    def create(self, task, now):
        with mock.patch.object(kit.time, "time", return_value=now):
            r = asyncio.run(self.shop.create({"line_items": [{"id": "G1", "quantity": 1}],
                                              "receipt": receipt(self.key, item="G1", task=task)}, "https://x"))
        return r.status_code, json.loads(r.body)

    def test_an_open_checkout_still_holds_its_unit(self):
        self.assertEqual(self.create("t-1", 1000)[0], 201)
        code, body = self.create("t-2", 1000 + shop_stock.PAY_SECONDS - 5)
        self.assertEqual(code, 409)
        self.assertIn("not in stock", body["error"])

    def test_a_checkout_older_than_the_sellers_wait_frees_its_unit(self):
        self.assertEqual(self.create("t-1", 1000)[0], 201)
        code, body = self.create("t-2", 1000 + kit.CHECKOUT_HOLD + 1)
        self.assertEqual(code, 201)
        self.assertEqual(body["fulfillment"]["unit"], "u-1")

    def test_the_hold_outlasts_the_sellers_wait(self):
        self.assertGreater(kit.CHECKOUT_HOLD, shop_stock.PAY_SECONDS)

    def test_the_old_checkout_cannot_be_paid_once_its_unit_moved_on(self):
        """Independent review High: the old session stayed ready_for_payment and both could reach /api/settle."""
        _, old = self.create("t-1", 1000)
        _, new = self.create("t-2", 1000 + kit.CHECKOUT_HOLD + 1)
        self.assertEqual(self.shop.sessions[old["id"]]["status"], "canceled")
        self.assertEqual(self.shop.sessions[new["id"]]["status"], "ready_for_payment")
        r = asyncio.run(self.shop.handle(mock.Mock(method="POST"), f"acp/checkout_sessions/{old['id']}/complete",
                                         b'{"payment_data": {"order": "o-1"}}', "https://x"))
        self.assertEqual(r.status_code, 409)

    def test_old_checkouts_leave_with_their_sessions(self):
        for i in range(505):
            self.shop.terms[f"t{i}"] = {}
            self.create(f"t{i}", 1000)
        self.assertLessEqual(len(self.shop.opened), 500)
        self.assertEqual(set(self.shop.opened), set(self.shop.sessions))

    def test_a_checkout_being_paid_keeps_its_unit_however_old(self):
        """Re-review Medium: a new deal could take the unit (and cancel the session) while its /api/settle was in flight."""
        entered, release = threading.Event(), threading.Event()

        class SlowSettle(OneUnitMarket):
            def call(self, method, path, role="buyer", **kw):
                if path == "/api/settle":
                    entered.set()
                    release.wait(5)
                    return httpx.Response(200, json={"ledger": 1, "mandate_receipt": {"ledger": 1}})
                return super().call(method, path, role, **kw)

        self.shop.market = SlowSettle()
        _, old = self.create("t-1", 1000)

        async def race():
            pay = asyncio.create_task(self.shop.handle(mock.Mock(method="POST"), f"acp/checkout_sessions/{old['id']}/complete",
                                                       b'{"payment_data": {"order": "o-1"}}', "https://x"))
            await asyncio.to_thread(entered.wait, 5)
            again = await self.shop.handle(mock.Mock(method="POST"), f"acp/checkout_sessions/{old['id']}/complete",
                                           b'{"payment_data": {"order": "o-1"}}', "https://x")
            with mock.patch.object(kit.time, "time", return_value=1000 + kit.CHECKOUT_HOLD + 1):
                new = await self.shop.create({"line_items": [{"id": "G1", "quantity": 1}],
                                              "receipt": receipt(self.key, item="G1", task="t-2")}, "https://x")
            release.set()
            return (await pay).status_code, again.status_code, new.status_code, json.loads(new.body)

        paid, again, code, body = asyncio.run(race())
        self.assertEqual((paid, again, code), (200, 409, 409))
        self.assertIn("not in stock", body["error"])
        self.assertEqual(self.shop.sessions[old["id"]]["status"], "completed")
        self.assertEqual(self.shop.paying, set())


if __name__ == "__main__":
    unittest.main()
