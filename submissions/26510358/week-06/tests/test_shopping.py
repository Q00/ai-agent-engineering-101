from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evidence import EvidenceError
from shopping import allocate_budget, select_shop, shop_all, validate_shopping_list
from tracing import Trace


class ShoppingTests(unittest.TestCase):
    def setUp(self):
        self.entries = [{"key": "lamp", "item": "lamp", "budget": 120},
                        {"key": "chair", "item": "chair", "budget": 200},
                        {"key": "bookshelf", "item": "bookshelf", "budget": 280}]
        self.directory = [
            {"id": f"shop-{index}", "url": f"http://127.0.0.1:{9000 + index}",
             "card": {"name": item.title(), "skills": [{"description": f"Sells {item}", "tags": [item]}]}}
            for index, item in enumerate(("lamp", "chair", "bookshelf"), start=1)
        ]

    def test_rejects_duplicate_slots_and_overallocated_total_before_model_calls(self):
        with self.assertRaisesRegex(EvidenceError, "duplicate"):
            validate_shopping_list([self.entries[0], self.entries[0]], 600)
        with self.assertRaisesRegex(EvidenceError, "sum"):
            validate_shopping_list(self.entries, 599)
        with self.assertRaises(EvidenceError):
            validate_shopping_list([{"key": "x", "item": "lamp", "budget": True}], 600)

    def test_selector_rejects_invented_shop_and_price(self):
        with tempfile.TemporaryDirectory() as temporary:
            trace = Trace("selector-test", temporary)
            def invented(context, **kwargs):
                return kwargs["validator"]({"action": "select_shop", "item_id": "invented", "price": None, "offer": None, "reason": "bad"})
            with patch("shopping.Harness") as harness:
                harness.return_value.decide.side_effect = invented
                with self.assertRaisesRegex(EvidenceError, "provided directory"):
                    select_shop(self.entries[0], self.directory, trace=trace)
                harness.return_value.decide.side_effect = lambda context, **kwargs: kwargs["validator"](
                    {"action": "select_shop", "item_id": "shop-1", "price": 110, "offer": None, "reason": "invalid price in selection"})
                with self.assertRaisesRegex(EvidenceError, "cannot specify"):
                    select_shop(self.entries[0], self.directory, trace=trace)

    def test_selection_reads_actual_skills_and_concurrent_buyers_share_one_ledger(self):
        barrier = threading.Barrier(3)
        seen = []
        seen_lock = threading.Lock()
        prices = {"lamp": 110, "chair": 190, "bookshelf": 270}

        def choose(context, **kwargs):
            self.assertEqual(len(context["directory"]), 3)
            shop = next(shop for shop in context["directory"]
                        if context["item"] in shop["card"]["skills"][0]["tags"])
            return kwargs["validator"]({"action": "select_shop", "item_id": shop["id"], "price": None, "offer": None, "reason": "skill match"})

        def fake_trade(url, item, budget, **kwargs):
            ledger = kwargs["ledger"]
            with seen_lock:
                seen.append(id(ledger))
            purchase_id = f"purchase-{item}"
            ledger.reserve(kwargs["item_key"], prices[item], purchase_id)
            barrier.wait(timeout=5)  # Fails if negotiations run sequentially.
            receipt = {"item": item, "price": prices[item]}
            ledger.commit(purchase_id, receipt)
            return {"status": "completed", "receipt": receipt}

        with tempfile.TemporaryDirectory() as temporary, patch("shopping.Harness") as harness, patch("shopping.negotiate", side_effect=fake_trade):
            harness.return_value.decide.side_effect = choose
            summary = shop_all(self.directory, self.entries, 600, log_dir=temporary)
        self.assertTrue(summary["passed"])
        self.assertEqual(summary["ledger"]["committed_total"], 570)
        self.assertEqual(summary["ledger"]["reserved_total"], 0)
        self.assertEqual(len(set(seen)), 1)
        self.assertEqual(len(summary["results"]), 3)

    def test_total_only_list_uses_harness_allocation_before_concurrent_purchases(self):
        entries = [{"key": entry["key"], "item": entry["item"]} for entry in self.entries]
        barrier = threading.Barrier(3)
        allocations = {"lamp": 120, "chair": 200, "bookshelf": 280}
        prices = {"lamp": 110, "chair": 190, "bookshelf": 270}
        roles = []

        class FakeHarness:
            def __init__(self, role, trace):
                self.role = role

            def decide(self, context, **kwargs):
                roles.append(self.role)
                if self.role == "budget_allocator":
                    self_assert.assertEqual(context["total_budget"], 600)
                    self_assert.assertNotIn("reserve", context)
                    self_assert.assertTrue(all("budget" not in entry for entry in context["shopping_list"]))
                    decision = {"action": "allocate_budget", "price": None, "item_id": None, "offer": None,
                                "reason": "test-only vector allocation",
                                "allocations": [{"key": key, "budget": budget} for key, budget in allocations.items()]}
                else:
                    shop_id = {"lamp": "shop-1", "chair": "shop-2", "bookshelf": "shop-3"}[context["item"]]
                    decision = {"action": "select_shop", "item_id": shop_id, "price": None, "offer": None, "reason": "test skill match"}
                return kwargs["validator"](decision)

        self_assert = self

        def fake_trade(url, item, budget, **kwargs):
            self.assertEqual(budget, allocations[item])
            self.assertLessEqual(kwargs["timeout"], 240)
            ledger = kwargs["ledger"]
            ledger.reserve(kwargs["item_key"], prices[item], f"purchase-{item}")
            barrier.wait(timeout=5)
            receipt = {"item": item, "price": prices[item]}
            ledger.commit(f"purchase-{item}", receipt)
            return {"status": "completed", "receipt": receipt}

        with tempfile.TemporaryDirectory() as temporary, patch("shopping.Harness", FakeHarness), patch("shopping.negotiate", side_effect=fake_trade):
            summary = shop_all(self.directory, entries, 600, log_dir=temporary)
        self.assertTrue(summary["passed"])
        self.assertEqual(roles[0], "budget_allocator")
        self.assertEqual(roles.count("budget_allocator"), 1)
        self.assertEqual(sum(entry["budget"] for entry in summary["allocated_envelopes"]), 600)

    def test_allocator_rejects_overbudget_duplicate_and_changed_explicit_envelopes(self):
        entries = [{"key": "lamp", "item": "lamp", "budget": 120},
                   {"key": "chair", "item": "chair"}]
        cases = [
            [{"key": "lamp", "budget": 120}, {"key": "chair", "budget": 500}],
            [{"key": "lamp", "budget": 120}, {"key": "lamp", "budget": 100}],
            [{"key": "lamp", "budget": 121}, {"key": "chair", "budget": 200}],
        ]
        with tempfile.TemporaryDirectory() as temporary, patch("shopping.Harness") as harness:
            for allocations in cases:
                harness.return_value.decide.side_effect = lambda context, allocations=allocations, **kwargs: kwargs["validator"](
                    {"action": "allocate_budget", "price": None, "item_id": None, "offer": None,
                     "reason": "invalid test allocation", "allocations": allocations})
                with self.subTest(allocations=allocations), self.assertRaises(EvidenceError):
                    allocate_budget(entries, self.directory, 600, trace=Trace("allocator-test", temporary))

    def test_no_deal_is_recorded_without_claiming_every_item_was_bought(self):
        def choose(context, **kwargs):
            return kwargs["validator"]({"action": "select_shop", "item_id": "shop-1", "price": None, "offer": None, "reason": "test"})
        with tempfile.TemporaryDirectory() as temporary, patch("shopping.Harness") as harness, patch("shopping.negotiate", return_value={"status": "no_deal"}):
            harness.return_value.decide.side_effect = choose
            summary = shop_all(self.directory, self.entries, 600, log_dir=temporary)
        self.assertFalse(summary["passed"])
        self.assertFalse(summary["all_items_bought"])
        self.assertEqual(summary["ledger"]["committed_total"], 0)


if __name__ == "__main__":
    unittest.main()
