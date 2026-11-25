"""Offline regressions for shelf-life, receipt identity and evidence accounting.

Run with the production venv; no Claude calls, keys or market writes.
"""
import asyncio
import base64
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import seller_r3 as seller
import shop_metrics as metrics


class ShelfLifeTests(unittest.TestCase):
    def setUp(self):
        self.now = 1_800_000_000
        self.unit = {"unit": "u-test", "sku": "Gtest", "name": "Test food", "category": "food>kimchi",
                     "kind": "food", "grade": "C", "cost": 60, "list_price": 100,
                     "supplied_at": self.now - 100, "expires_at": self.now + 20 * 3600}

    def test_requirement_parser_does_not_confuse_prices_and_hours(self):
        self.assertEqual(seller.need_hours("must last 12.5 hours"), 12.5)
        self.assertEqual(seller.need_hours("use within 36 hours"), 36)
        self.assertEqual(seller.need_hours("use_within_hours=24"), 24)
        self.assertIsNone(seller.need_hours("last price 10000"))

    def test_short_explicit_need_is_rejected_without_wrong_sku_fallback(self):
        other = dict(self.unit, unit="u-other", sku="Gother", expires_at=self.now + 72 * 3600)
        with patch.object(seller.time, "time", return_value=self.now), patch.object(
                seller, "inventory", return_value={"round": 3, "units": [self.unit, other]}):
            self.assertIsNone(seller.find_unit("Gtest must last 36 hours"))
            self.assertEqual(seller.find_unit("Gtest must last 12 hours")["unit"], "u-test")

    def test_grade_c_is_allowed_when_it_meets_the_real_requirement(self):
        with patch.object(seller.time, "time", return_value=self.now), patch.object(
                seller, "inventory", return_value={"round": 3, "units": [self.unit]}):
            self.assertEqual(seller.find_unit("Gtest must last 12 hours")["grade"], "C")

    def test_shelf_life_is_rechecked_before_receipt_signing(self):
        executor = seller.Seller()
        task = types.SimpleNamespace(id="task-test")
        deal = {"unit": self.unit, "food_need": 36}
        executor.open[task.id] = deal
        messages = []

        class Updater:
            async def reject(self, message):
                messages.append(message)

        with patch.object(seller.time, "time", return_value=self.now), patch.object(seller, "sign") as sign:
            asyncio.run(executor._close(task, Updater(), lambda s: s, deal, 70))
        self.assertEqual(len(messages), 1)
        self.assertNotIn(task.id, executor.open)
        sign.assert_not_called()

    def test_category_receipt_keeps_identity_and_explicit_sku_wins(self):
        self.assertEqual(seller.receipt_item("I want kimchi", self.unit), "kimchi")
        self.assertEqual(seller.receipt_item("I want kimchi Gtest", self.unit), "Gtest")
        self.assertEqual(seller.receipt_item("I want Test food", self.unit), "Gtest")

    @unittest.skipUnless(os.environ.get("MARKET_KIT_PATH"), "set MARKET_KIT_PATH for ACP integration")
    def test_signed_category_receipt_checks_out_as_the_bound_sku(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        spec = importlib.util.spec_from_file_location("kit_for_test", os.environ["MARKET_KIT_PATH"])
        kit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(kit)
        unit = dict(self.unit, cert={"fixture": True})

        class Market:
            def call(self, method, path, role):
                assert (method, path, role) == ("GET", "/api/inventory", "shop")
                return types.SimpleNamespace(status_code=200, json=lambda: {"round": 3, "units": [unit]})

        key = Ed25519PrivateKey.generate()
        body = {"shop": "kimauve", "item": "kimchi", "price": 70, "task": "task-test"}
        receipt = dict(body, sig_seller=base64.b64encode(key.sign(kit.canon(body))).decode())
        acp = kit.AcpShop(key, Market())
        acp.terms["task-test"] = dict(receipt, unit=unit["unit"])
        response = asyncio.run(acp.create({"receipt": receipt, "line_items": [{"id": "kimchi", "quantity": 1}],
                                           "acceptable": ["Gtest"]}, "https://test.invalid"))
        self.assertEqual(response.status_code, 201)
        checkout = kit.jwt_payload(json.loads(response.body)["checkout_jwt"], kit.pub_b64(key))
        self.assertEqual((checkout["item"], checkout["unit"], checkout["price"]), ("Gtest", "u-test", 70))

    def test_deadline_does_not_force_below_cost_goods_sale(self):
        unit = dict(self.unit, kind="goods", expires_at=None)
        with patch.object(seller.time, "time", return_value=seller.END_TS - 3600), \
                patch.object(seller, "base_share", return_value=.85), \
                patch.object(seller, "scarcity", return_value=1), \
                patch.object(seller, "pick_arm", return_value=(1, .5)), \
                patch.object(seller, "max_share", return_value=.95), \
                patch.object(seller, "rival_ask", return_value=None), \
                patch.object(seller, "warranty_for", return_value=0), \
                patch.object(seller, "_load", return_value={}):
            ask, floor, _, _ = seller.terms(unit)
        self.assertGreaterEqual(floor, unit["cost"])
        self.assertGreaterEqual(ask, floor)


class EvidenceTests(unittest.TestCase):
    def test_accounts_merge_but_unverified_reviews_do_not_count(self):
        def review(seq, buyer, stars, verified=True):
            return {"seq": seq, "ts": seq, "kind": "result",
                    "data": {"buyer": buyer, "order": str(seq), "stars": stars, "verified": verified}}
        events = {r["seq"]: r for r in (review(1, "a", 5), review(2, "a2", 1),
                                        review(3, "b", 5), review(4, "c", 1, False))}
        report = metrics.summarize(events, {}, {"a": "owner-a", "a2": "owner-a"})
        self.assertEqual(report["observed_account_mean"], 4)
        self.assertEqual(report["review_count"], 3)
        self.assertEqual(report["matched_settlements"], 0)

    def test_failed_ledger_page_does_not_skip_missing_history(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            folder = Path(folder)
            def get(path):
                if path == "/api/board":
                    return {"shops": [{"name": "kimauve", "card": {"url": "https://shop"}}], "buyers": []}
                if path == "/api/ledger?after=-1":
                    return [{"seq": 1000}]
                if path.endswith("after=0"):
                    return [{"seq": 500, "ts": 1, "kind": "result", "data": {"shop": "other"}}]
                raise OSError("temporary outage")
            with patch.object(metrics, "get", side_effect=get):
                report = metrics.run(folder, 2, "kimauve")
            self.assertEqual(report["backlog"], 500)
            self.assertEqual(metrics.load(folder / ".market/shop-metrics-cursor.json", {})["seq"], 500)
            self.assertEqual(len(report["errors"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
