"""The A-grade six-hour warranty: off by default, honest on the receipt and in the real kit's checkout, inside its own limits, and apart from the two-hour trial. No payment."""

import base64
import json
import os
from pathlib import Path
import runpy
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from market_state import read, task_file, write
import warranty_trial

ON = {"enabled": True, "hours": 6, "max_exposure": 1000000, "cash_exposure_ratio": .1, "refund_ratio": .02, "quote_lease_seconds": 600}


class Settings(unittest.TestCase):
    def test_shipped_off_with_the_trials_limits(self):
        value = json.loads((Path(warranty_trial.__file__).parent / "a_grade_warranty.json").read_text(encoding="utf-8"))
        # Switched on with the user's approval of 11-10: 60M (the validator maximum) and .5 of the balance.
        self.assertIs(value["enabled"], True)
        self.assertEqual((value["hours"], value["max_exposure"], value["cash_exposure_ratio"], value["refund_ratio"]), (6, 60000000, .5, .02))
        self.assertEqual(warranty_trial.six_hour_settings()["max_exposure"], 60000000)

    def test_anything_but_a_valid_setting_is_off(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "w.json"
            self.assertIsNone(warranty_trial.six_hour_settings(path))
            for bad in ({**ON, "enabled": "true"}, {**ON, "enabled": 1}, {**ON, "hours": 2}, {**ON, "hours": 7}, {**ON, "max_exposure": 0}, {**ON, "max_exposure": 60000001}, {**ON, "cash_exposure_ratio": .71},
                        {**ON, "refund_ratio": .2}, {**ON, "refund_ratio": -.1}, {**ON, "quote_lease_seconds": 60}, {"enabled": True}, {**ON, "max_exposure": "x"}, []):
                path.write_text(json.dumps(bad), encoding="utf-8")
                self.assertIsNone(warranty_trial.six_hour_settings(path), bad)
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(ValueError):  # market_state.read raises on a broken file and the seller then refuses the deal; it never promises a warranty.
                warranty_trial.six_hour_settings(path)
            path.write_text(json.dumps(ON), encoding="utf-8")
            self.assertEqual(warranty_trial.six_hour_settings(path)["hours"], 6)


class Assignment(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.md = Path(self.dir.name) / ".market"
        self.now = time.time()
        self.unit = {"unit": "u1", "sku": "G1", "kind": "goods", "grade": "A", "cert": True, "cost": 100, "supplied_at": self.now}
        self.terms = {"quote": 200, "floor": 105}
        self.inventory(5000000)

    def inventory(self, balance, unit=None):
        write(self.md / "inventory.json", {"round": 3, "balance": balance, "units": [unit or self.unit]})
        os.utime(self.md / "inventory.json", (self.now, self.now))

    def assign(self, task="t1", unit=None, terms=None, setting=ON, now=None):
        with patch.object(warranty_trial, "six_hour_settings", return_value=setting):
            return warranty_trial.assignment(self.md, task, unit or self.unit, terms or self.terms, now or self.now)

    def test_on_gives_six_hours_with_its_floor(self):
        value = self.assign()
        self.assertEqual((value["hours"], value["group"], value["enrolled"]), (6, "six_hour", False))
        self.assertEqual(value["floor"], 105)
        self.assertEqual(warranty_trial.liability(self.md, self.now), 200)

    def test_refund_ratio_can_raise_the_floor_but_only_above_the_ordinary_one(self):
        self.assertEqual(self.assign(setting={**ON, "refund_ratio": .1})["floor"], 112)

    def test_off_the_two_hour_trial_behaves_as_before(self):
        with patch.object(warranty_trial, "config", return_value={"enabled": False}):
            value = self.assign(setting=None)
        self.assertEqual(value, {"hours": 0, "floor": 105, "enrolled": False})

    def test_only_a_grade_goods_with_a_certificate(self):
        for change in ({"grade": "B"}, {"grade": "C"}, {"kind": "food"}, {"cert": None}):
            with patch.object(warranty_trial, "config", return_value={"enabled": False}):
                self.assertEqual(self.assign(unit={**self.unit, **change})["hours"], 0, change)

    def test_a_clearance_below_cost_gets_none(self):
        with patch.object(warranty_trial, "config", return_value={"enabled": False}):
            self.assertEqual(self.assign(terms={**self.terms, "below_cost_allowed": True})["hours"], 0)

    def test_a_quote_under_the_floor_gets_none(self):
        self.assertEqual(self.assign(terms={"quote": 100, "floor": 90}, setting={**ON, "refund_ratio": .1})["hours"], 0)

    def test_a_stale_inventory_gets_none(self):
        os.utime(self.md / "inventory.json", (self.now-500, self.now-500))
        self.assertEqual(self.assign()["hours"], 0)

    def test_the_exposure_limit_stops_the_next_one(self):
        limit = {**ON, "max_exposure": 300}
        self.assertEqual(self.assign("t1", setting=limit)["hours"], 6)
        self.assertEqual(self.assign("t2", setting=limit)["hours"], 0)

    def test_the_cash_ratio_stops_it(self):
        self.inventory(1000)
        self.assertEqual(self.assign()["hours"], 0)

    def test_the_cash_reserve_stops_it(self):
        self.inventory(1000100)
        self.assertEqual(self.assign(setting={**ON, "max_exposure": 60000000, "cash_exposure_ratio": .7})["hours"], 0)

    def test_the_same_task_keeps_its_promise_and_expires_plainly(self):
        first = self.assign()
        self.assertEqual(self.assign(setting=None), first)
        with self.assertRaises(ValueError):
            self.assign(now=self.now+600)

    def test_a_trial_pause_does_not_take_back_six_hours_or_the_reverse(self):
        write(self.md / "warranty-trial.json", {"assignments": {}, "paused": True})
        self.assertEqual(self.assign()["hours"], 6)

    def test_on_nothing_new_enters_the_two_hour_trial(self):
        self.assign("t1")
        state = read(self.md / "warranty-trial.json")
        self.assertEqual([a["group"] for a in state["assignments"].values()], ["six_hour"])

    def test_the_trials_evaluation_does_not_count_or_trip_on_it(self):
        self.assign("t1")
        write(self.md / "signed-offers" / "t1.json", {"task": "t1", "unit": "u1", "sku": "G1", "price": 200, "cost": 100, "issued_at": self.now, "expires_at": self.now+600, "warranty_hours": 6})
        result = warranty_trial.evaluate(self.md, self.now)
        self.assertEqual((result["warranty_mature_total"], result["control_mature_total"]), (0, 0))
        self.assertIsNot(read(self.md / "warranty-trial.json").get("paused"), True)

    def test_a_signed_sale_stays_reserved_for_six_hours_and_then_clears(self):
        self.assign("t1")
        write(self.md / "signed-offers" / "t1.json", {"task": "t1", "unit": "u1", "sku": "G1", "price": 200, "cost": 100, "issued_at": self.now, "expires_at": self.now+600, "warranty_hours": 6})
        self.assertEqual(warranty_trial.liability(self.md, self.now+6*3600), 200)
        self.assertEqual(warranty_trial.liability(self.md, self.now+6*3600+601), 0)


class Receipt(unittest.IsolatedAsyncioTestCase):
    async def test_six_hours_reach_the_notice_the_receipt_and_the_offer(self):
        import seller_r3
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            md = root / ".market"
            now = time.time()
            unit = {"unit": "u", "sku": "G1", "name": "fixture", "category": "g", "grade": "A", "cert": True, "cost": 100, "kind": "goods", "list_price": 200, "supplied_at": now, "expires_at": None}
            write(md / "inventory.json", {"round": 3, "balance": 5000000, "units": [unit]})
            write(md / "catalog.json", {"G1": {"kind": "goods", "list_price": 200}})
            updater = MagicMock()
            updater.requires_input, updater.complete, updater.add_artifact, updater.reject = AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock()
            context = SimpleNamespace(current_task=SimpleNamespace(id="t1", context_id="c"), message=None)
            seller = seller_r3.Seller()
            with patch.object(seller_r3, "MARKET_DIR", md), patch.object(seller_r3, "__file__", str(root / "seller_r3.py")), patch.object(seller_r3, "TaskUpdater", return_value=updater), \
                    patch.object(seller_r3, "get_message_text", side_effect=["buy G1", "200"]), patch.object(seller_r3, "decision_mode", return_value="rules"), \
                    patch.object(seller_r3, "capped", side_effect=lambda terms, u: terms), patch.object(warranty_trial, "six_hour_settings", return_value=ON), \
                    patch.object(seller_r3, "sign", side_effect=lambda r: {**r, "sig_seller": "fixture"}) as sign, patch.object(seller_r3, "new_text_part", side_effect=lambda text: text) as text:
                await seller.execute(context, MagicMock())
                self.assertIn("Warranty: 6 hours.", text.call_args_list[0].args[0])
                await seller.execute(context, MagicMock())
            receipt = sign.call_args.args[0]
            self.assertEqual((receipt["claim"], receipt["warranty_hours"], receipt["price"]), ("A", 6, 200))
            self.assertEqual(read(task_file(md, "signed-offers", "t1"))["warranty_hours"], 6)


class InTheKit(unittest.IsolatedAsyncioTestCase):
    """The market's own checkout code with a fake inventory. The kit signs the warranty into the checkout, and a buyer's policy reads it there. Nothing is paid."""

    def setUp(self):
        self.ns = runpy.run_path(str(Path(__file__).resolve().parents[3] / "scripts/market_kit.py"), run_name="kit_test")
        self.g = self.ns["main"].__globals__
        self.key = Ed25519PrivateKey.generate()
        self.public = base64.b64encode(self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
        unit = {"unit": "u1", "sku": "G1", "name": "n", "category": "a>b", "grade": "A", "cert": {"cert": {"unit": "u1"}, "sig": "x"}, "cost": 100, "kind": "goods", "list_price": 200, "supplied_at": 1, "expires_at": None}
        inventory = SimpleNamespace(status_code=200, json=lambda: {"round": 3, "units": [unit]})
        self.acp = self.g["AcpShop"](self.key, SimpleNamespace(call=lambda *a, **k: inventory), None)

    async def checkout(self, hours, sent_by_buyer=None):
        body = {"shop": "kcwarni Inventory Shop", "item": "G1", "price": 200, "task": "t1"}
        signed = dict(body, sig_seller=base64.b64encode(self.key.sign(self.g["canon"](body))).decode())
        self.acp.terms["t1"] = {"unit": "u1", "claim": "A", "cert": True, "warranty_hours": hours}
        receipt = signed if sent_by_buyer is None else dict(signed, warranty_hours=sent_by_buyer)
        result = await self.acp.create({"line_items": [{"id": "G1"}], "receipt": receipt, "acceptable": ["G1"]}, "https://mine")
        self.assertEqual(result.status_code, 201)
        return json.loads(result.body), self.g["jwt_payload"](json.loads(result.body)["checkout_jwt"], self.public)

    async def test_the_signed_checkout_carries_six_hours(self):
        session, co = await self.checkout(6)
        self.assertEqual((co["warranty_hours"], co["claim"], session["fulfillment"]["warranty_hours"]), (6, "A", 6))

    async def test_a_buyer_cannot_write_its_own_warranty(self):
        _, co = await self.checkout(6, sent_by_buyer=72)
        self.assertEqual(co["warranty_hours"], 6)

    async def test_no_warranty_is_left_out_of_the_checkout(self):
        _, co = await self.checkout(0)
        self.assertNotIn("warranty_hours", co)

    async def test_a_buyer_asking_six_hours_passes_and_one_asking_twelve_is_refused(self):
        _, co = await self.checkout(6)
        check = self.g["check_accept"]
        order = {"kind": "goods", "market_pubkey": self.public}
        self.assertIsNone(check(order, {k: v for k, v in co.items() if k != "cert"}, {"warranty_hours": 6}))
        self.assertIn("warranty 6 h", check(order, {k: v for k, v in co.items() if k != "cert"}, {"warranty_hours": 12}))


if __name__ == "__main__":
    unittest.main()
