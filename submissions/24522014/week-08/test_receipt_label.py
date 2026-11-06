"""The receipt's item: SKU orders unchanged, category orders named as asked, nothing mislabeled, an off switch, and the real kit's checkout accepting it. No payment."""

import base64
import hashlib
import json
from pathlib import Path
import runpy
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from market_state import read, task_file, write
import receipt_label as label

UNIT = {"unit": "u1", "sku": "G0be9ed07", "name": "fixture kimchi", "category": "식품>김치/반찬/즉석요리>김치", "grade": "A", "cert": {"cert": {"unit": "u1", "sku": "G0be9ed07"}, "sig": "x"}, "cost": 100, "kind": "goods", "list_price": 200,
        "supplied_at": 1, "expires_at": None}


class Form(unittest.TestCase):
    def on(self, folder):
        path = Path(folder) / "s.json"
        path.write_text('{"enabled": true}', encoding="utf-8")
        return path

    def test_forms(self):
        for text, form in (("I want to buy G0BE9ED07", "sku"), ("I want to buy 김치 (G0be9ed07)", "sku"), ("I want to buy 김치, not Gabcdef01", "sku"), ("I want to buy 김치", "category"), ("김치", "category"), ("김치.", "category"),
                           ("I want to buy 오이김치", "other"), ("김치찌개 있나요", "other"), ("fixture kimchi please", "other"), ("", "other"), (None, "other")):
            self.assertEqual(label.request_form(text, UNIT), form, text)

    def test_a_named_sku_keeps_the_sku(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(label.receipt_item("I want to buy 김치 G0be9ed07", UNIT, self.on(folder)), "G0be9ed07")

    def test_a_category_order_gets_the_units_own_category(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(label.receipt_item("I want to buy 김치", UNIT, self.on(folder)), "김치")

    def test_case_of_the_category_is_ours_not_the_buyers(self):
        unit = dict(UNIT, category="Goods>Lamps")
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(label.receipt_item("i want lamps", unit, self.on(folder)), "Lamps")

    def test_a_request_that_fits_by_name_or_not_at_all_keeps_the_sku(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.on(folder)
            for text in ("fixture kimchi please", "I want to buy 오이김치", "chairs"):
                self.assertEqual(label.receipt_item(text, UNIT, path), "G0be9ed07", text)

    def test_a_unit_without_a_category_or_id_keeps_the_sku(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.on(folder)
            self.assertEqual(label.receipt_item("anything", dict(UNIT, category=""), path), "G0be9ed07")
            self.assertEqual(label.receipt_item("김치", dict(UNIT, unit=None), path), "G0be9ed07")

    def test_off_missing_or_broken_setting_means_sku(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "s.json"
            self.assertEqual(label.receipt_item("김치", UNIT, path), "G0be9ed07")
            for text in ('{"enabled": false}', '{"enabled": "true"}', '{"enabled": 1}', "{}", "[]", "not json", ""):
                path.write_text(text, encoding="utf-8")
                self.assertEqual(label.receipt_item("김치", UNIT, path), "G0be9ed07", text)


class InSeller(unittest.IsolatedAsyncioTestCase):
    async def sell(self, text, setting='{"enabled": true}'):
        import seller_r3
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            md = root / ".market"
            unit = dict(UNIT, cert=True, supplied_at=time.time())
            write(md / "inventory.json", {"round": 3, "balance": 5000000, "units": [unit]})
            write(md / "catalog.json", {unit["sku"]: {"kind": "goods", "list_price": 200}})
            config = root / "receipt_label.json"
            if setting is not None:
                config.write_text(setting, encoding="utf-8")
            task = next(f"task-{i}" for i in range(100) if int(hashlib.sha256(f"task-{i}".encode()).hexdigest(), 16) % 2)
            updater = MagicMock()
            updater.requires_input = AsyncMock()
            updater.complete = AsyncMock()
            updater.add_artifact = AsyncMock()
            updater.reject = AsyncMock()
            context = SimpleNamespace(current_task=SimpleNamespace(id=task, context_id="c"), message=None)
            seller = seller_r3.Seller()
            quote = {"floor": 100, "quote": 200, "discount_stage": 0, "discount_reason": "standard", "strategy_version": 1, "strategy_experiment": None, "below_cost_allowed": False}
            coverage = {"hours": 0, "floor": 100, "enrolled": False, "group": None}
            with patch.object(seller_r3, "MARKET_DIR", md), patch.object(seller_r3, "__file__", str(root / "seller_r3.py")), patch.object(label, "CONFIG", config), \
                    patch.object(seller_r3, "receipt_item", side_effect=lambda t, u: label.receipt_item(t, u, config)), \
                    patch.object(seller_r3, "TaskUpdater", return_value=updater), patch.object(seller_r3, "get_message_text", side_effect=[text, "200"]), \
                    patch.object(seller_r3, "decision_mode", return_value="rule"), patch.object(seller_r3, "selling_terms", return_value=quote), \
                    patch("warranty_trial.assignment", return_value=coverage), \
                    patch.object(seller_r3, "sign", side_effect=lambda r: {**r, "sig_seller": "fixture"}) as sign:
                await seller.execute(context, MagicMock())
                await seller.execute(context, MagicMock())
            return sign.call_args.args[0], read(task_file(md, "signed-offers", task))

    async def test_sku_order_is_unchanged(self):
        receipt, offer = await self.sell("I want to buy G0be9ed07")
        self.assertEqual(receipt["item"], "G0be9ed07")
        self.assertEqual((offer["sku"], offer["receipt_item"], offer["request_form"]), ("G0be9ed07", "G0be9ed07", "sku"))

    async def test_category_order_keeps_everything_else(self):
        receipt, offer = await self.sell("I want to buy 김치")
        self.assertEqual(receipt["item"], "김치")
        self.assertEqual((receipt["unit"], receipt["claim"], receipt["cert"], receipt["warranty_hours"], receipt["price"]), ("u1", "A", True, 0, 200))
        self.assertEqual((offer["sku"], offer["unit"], offer["receipt_item"], offer["request_form"]), ("G0be9ed07", "u1", "김치", "category"))

    async def test_switch_off_goes_back_to_the_sku(self):
        for setting in ('{"enabled": false}', None, "broken"):
            receipt, offer = await self.sell("I want to buy 김치", setting)
            self.assertEqual(receipt["item"], "G0be9ed07", setting)
            self.assertEqual((offer["receipt_item"], offer["request_form"]), ("G0be9ed07", "category"))


class InTheKit(unittest.IsolatedAsyncioTestCase):
    """The market's own checkout code, with a fake market answering the inventory call. Nothing is paid."""

    def setUp(self):
        self.ns = runpy.run_path(str(Path(__file__).resolve().parents[3] / "scripts/market_kit.py"), run_name="kit_test")
        self.g = self.ns["main"].__globals__
        self.key = Ed25519PrivateKey.generate()
        self.public = base64.b64encode(self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
        inventory = SimpleNamespace(status_code=200, json=lambda: {"round": 3, "units": [dict(UNIT)]})
        self.acp = self.g["AcpShop"](self.key, SimpleNamespace(call=lambda *a, **k: inventory), None)

    def receipt(self, item, task="t1"):
        body = {"shop": "kcwarni Inventory Shop", "item": item, "price": 200, "task": task}
        signed = dict(body, sig_seller=base64.b64encode(self.key.sign(self.g["canon"](body))).decode())
        self.acp.terms[task] = {"unit": "u1", "claim": "A", "cert": True, "warranty_hours": 6}
        return signed

    async def checkout(self, item, acceptable):
        receipt = self.receipt(item)
        result = await self.acp.create({"line_items": [{"id": receipt["item"]}], "receipt": receipt, "acceptable": acceptable}, "https://mine")
        return result, receipt

    def jwt(self, result):
        return self.g["jwt_payload"](json.loads(result.body)["checkout_jwt"], self.public)

    @staticmethod
    def buyer_signs(receipt, order):
        # market_kit.py:746: the condition under which a buyer's kit signs a receipt.
        return not (receipt.get("item") != order["item"] and receipt.get("item") not in (order.get("acceptable") or []))

    @staticmethod
    def buyer_pays(co, receipt, order):
        # market_kit.py:511: the condition under which a buyer's kit pays a checkout.
        return co and (co.get("item") == receipt["item"] or co.get("item") in (order.get("acceptable") or []))

    async def test_a_category_receipt_goes_through_with_the_sku_in_the_checkout(self):
        order = {"item": "김치", "acceptable": ["G0be9ed07"]}
        result, receipt = await self.checkout("김치", order["acceptable"])
        self.assertEqual(result.status_code, 201)
        co = self.jwt(result)
        self.assertEqual((co["item"], co["unit"], co["claim"], co["warranty_hours"]), ("G0be9ed07", "u1", "A", 6))
        self.assertTrue(self.buyer_signs(receipt, order))
        self.assertTrue(self.buyer_pays(co, receipt, order))

    async def test_a_sku_receipt_still_goes_through(self):
        order = {"item": "김치", "acceptable": ["G0be9ed07"]}
        result, receipt = await self.checkout("G0be9ed07", order["acceptable"])
        self.assertEqual(result.status_code, 201)
        self.assertTrue(self.buyer_signs(receipt, order))
        self.assertTrue(self.buyer_pays(self.jwt(result), receipt, order))

    async def test_a_unit_the_order_does_not_accept_is_refused_even_under_its_name(self):
        result, _ = await self.checkout("김치", ["Gffffffff"])
        self.assertEqual(result.status_code, 409)

    async def test_a_label_for_another_category_is_not_signed_by_the_buyer(self):
        self.assertFalse(self.buyer_signs({"item": "김치"}, {"item": "오이김치", "acceptable": ["Gffffffff"]}))

    async def test_without_acceptable_the_category_must_be_the_units(self):
        result, _ = await self.checkout("김치", None)
        self.assertEqual(result.status_code, 201)
        receipt = self.receipt("침구", "t2")
        refused = await self.acp.create({"line_items": [{"id": "침구"}], "receipt": receipt}, "https://mine")
        self.assertEqual(refused.status_code, 409)


if __name__ == "__main__":
    unittest.main()
