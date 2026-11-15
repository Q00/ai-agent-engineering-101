"""Operational overlays affect only newly opened tasks, using synthetic stock."""
from copy import deepcopy
import json
import time
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from feedback import atomic_json
from opening_quote_cache import OpeningQuoteCache
from seller_agent import DEFAULT, build
from shop_ops_policy import digest
from shop_policy import policy_at
from test_adaptive_seller import send
from test_inventory import unit
import test_closing as fixtures


def overlay(version="v1", discount=-1):
    return {"version": version, "baseline_hash": digest(policy_at()), "changes": [{"sku": "G00001", "grade": "B",
            "warranty_hours": 0, "before": 0, "after": discount}]}


def reference(target=80000):
    return {"target": target, "source": "own_settled_sales", "created_at": time.time(), "measured": True}


class OperationsExecutionTests(unittest.TestCase):
    setUp = fixtures.ClosingTests.setUp
    seller = fixtures.ClosingTests.seller

    def test_reloaded_overlay_changes_only_new_task_and_preserves_signed_terms(self):
        seller, key = self.seller()
        first_unit, second_unit = unit(), unit(unit="u2")
        second_unit["cert"] = {"cert": {"unit": "u2", "sku": "G00001", "grade": "B"}, "sig": "fixture"}
        atomic_json(self.root / "inventory.json", {"round": 3, "units": [first_unit, second_unit]})
        active = [overlay()]
        with patch("seller_r3.read_overlay", side_effect=lambda *a, **kw: active[0]) as reader, \
                patch.object(seller.openings, "for_product", return_value=reference()), \
                patch("seller_r3.inventory_decision", side_effect=AssertionError("no trading model needed")), \
                TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
            pinned = deepcopy(seller.r3[first["id"]])
            self.assertEqual(first["status"]["message"]["metadata"]["ask"], 79200)
            active[0] = overlay("v2", -2)
            second = send(client, "G00001")
            self.assertEqual(second["status"]["message"]["metadata"]["ask"], 78400)
            self.assertEqual(seller.r3[first["id"]]["terms"], pinned["terms"])
            self.assertEqual(seller.r3[first["id"]]["opening_bounds"], pinned["opening_bounds"])
            self.assertEqual(seller.r3[first["id"]]["ops_policy_version"], "v1")
            self.assertEqual(seller.r3[second["id"]]["ops_policy_version"], "v2")
            completed = send(client, "79200", first["id"])
            receipt = fixtures.ClosingTests.receipt(self, completed, key, 79200)
            self.assertEqual(receipt["task"], first["id"])
            self.assertEqual(receipt["unit"], "u1")
            self.assertEqual(receipt.get("warranty_hours", 0), pinned["terms"]["warranty_hours"])
            self.assertEqual(reader.call_count, 2)
        events = [json.loads(line) for line in seller.events.read_text().splitlines()]
        settled_task = next(e for e in events if e["outcome"] == "receipt_issued")
        self.assertEqual(settled_task["ops_policy_version"], "v1")
        self.assertEqual(settled_task["ops_opening_before"]["target"], 80000)
        self.assertEqual(settled_task["ops_opening_after"]["target"], 79200)

    def test_missing_malformed_or_failed_reader_keeps_baseline_quote(self):
        cases = [None, {"version": "v1", "changes": "invalid"}, overlay(discount=-20),
                 {**overlay(), "baseline_hash": "old-baseline"},
                 {**overlay(), "changes": overlay()["changes"] * 2}, OSError("reader failed")]
        for value in cases:
            with self.subTest(value=value):
                seller, _ = self.seller()
                options = {"side_effect": value} if isinstance(value, Exception) else {"return_value": value}
                with patch("seller_r3.read_overlay", **options), \
                        patch.object(seller.openings, "for_product", return_value=reference()), \
                        TestClient(build(DEFAULT, seller)) as client:
                    first = send(client, "G00001")
                self.assertEqual(first["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
                self.assertEqual(first["status"]["message"]["metadata"]["ask"], 80000)
                self.assertIsNone(seller.r3[first["id"]].get("ops_policy_version"))

    def test_discount_never_changes_floor_and_needs_fresh_measured_reference(self):
        seller, _ = self.seller()
        with patch("seller_r3.read_overlay", return_value=overlay(discount=-2)), \
                patch.object(seller.openings, "for_product", return_value=reference(73500)), \
                TestClient(build(DEFAULT, seller)) as client:
            first = send(client, "G00001")
        deal = seller.r3[first["id"]]
        self.assertEqual(deal["terms"]["price_floor"], 73500)
        self.assertEqual(deal["terms"]["closing_price_floor"], 71400)
        self.assertEqual(first["status"]["message"]["metadata"]["ask"], 73500)
        for ref in (None, {**reference(), "created_at": time.time()-3601},
                    {**reference(), "measured": False}, {**reference(), "source": "manual_pilot_policy"}):
            seller, _ = self.seller()
            with patch("seller_r3.read_overlay", return_value=overlay()) as reader, \
                    patch.object(seller.openings, "for_product", return_value=ref), \
                    patch("seller_r3.inventory_decision", return_value=fixtures.counter(80000)), \
                    TestClient(build(DEFAULT, seller)) as client:
                send(client, "G00001")
            reader.assert_not_called()

    def test_cache_key_includes_policy_version_even_if_clamped_price_does_not_change(self):
        product = unit()
        deal = {"round": 3, "terms": {}, "negotiation": {}, "ops_policy_version": "v1"}
        cache = OpeningQuoteCache(clock=lambda: 1)
        key = cache.key(product, deal)
        cache.remember(key, 73500)
        self.assertIsNone(cache.get(cache.key(product, {**deal, "ops_policy_version": "v2"})))


if __name__ == "__main__":
    unittest.main()
