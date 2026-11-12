"""Catalog requirements, model boundaries, A2A artifacts and cancellation."""
import asyncio
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from a2a.helpers import new_text_part
from a2a.types import Message
from starlette.testclient import TestClient

from catalog import Catalog, matches, parse_query, parse_selection, public_product, select_product
from catalog_seller import CatalogSeller, DEFAULT, build
from generate_catalog import generate
from harness import HarnessError
from test_seller import send, task


class CatalogRetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog.from_config({})

    def test_committed_catalog_is_reproducible_and_distinct(self):
        self.assertEqual(self.catalog.products, generate())
        self.assertEqual(len(self.catalog.products), 4000)
        self.assertEqual(len({product["id"] for product in self.catalog.products}), 4000)
        self.assertEqual(len({product["name"] for product in self.catalog.products}), 4000)

    def test_constrained_search_counts_all_matches_but_exposes_at_most_eight(self):
        examples = [
            {"category": "bookshelf", "material": "wood", "min_capacity_books": 40, "max_height_cm": 75},
            {"category": "lamp", "material": "metal", "max_height_cm": 55},
            {"category": "chair", "material": "fabric", "upholstered": True, "max_height_cm": 100},
        ]
        for constraints in examples:
            with self.subTest(constraints=constraints):
                result = self.catalog.search(constraints)
                expected = [product for product in self.catalog.products if matches(product, constraints)]
                self.assertEqual(result["matched_count"], len(expected))
                self.assertGreater(result["matched_count"], 8)
                self.assertEqual(len(result["candidates"]), 8)
                self.assertTrue(all(matches(product, constraints) for product in result["candidates"]))
        impossible = self.catalog.search({"category": "bookshelf", "min_capacity_books": 200, "max_height_cm": 20})
        self.assertEqual(impossible["matched_count"], 0)
        self.assertEqual(impossible["candidates"], [])

    def test_twenty_shop_partitions_cover_inventory_without_duplicate_stock(self):
        seen = set()
        for category in ("lamp", "chair", "bookshelf", "table"):
            for variant in range(5):
                shop = Catalog.from_config({"categories": [category], "inventory_mod": 5,
                                            "inventory_remainder": variant, "reserve_offset": variant * 5})
                self.assertEqual(len(shop.products), 200)
                self.assertFalse(seen & set(shop.by_id))
                seen.update(shop.by_id)
                for product in shop.products:
                    self.assertEqual(product["reserve"], self.catalog.by_id[product["id"]]["reserve"] + variant * 5)
        self.assertEqual(seen, set(self.catalog.by_id))

    def test_model_cannot_invent_item_or_violate_its_reserve(self):
        constraints = {"category": "bookshelf", "material": "wood", "min_capacity_books": 40, "max_height_cm": 75}
        candidates = self.catalog.search(constraints)["candidates"]
        product = candidates[0]
        valid = {"action": "select", "product_id": product["id"], "price": product["reserve"], "reason": "All requirements met"}
        self.assertEqual(parse_selection(json.dumps(valid), candidates, constraints)["product"], product)
        for changes in ({"product_id": "lamp-0001"}, {"price": product["reserve"] - 1}, {"price": True}, {"price": "200"}):
            with self.subTest(changes=changes), self.assertRaises(HarnessError):
                parse_selection(json.dumps({**valid, **changes}), candidates, constraints)
        with self.assertRaises(HarnessError):
            parse_selection(json.dumps(valid), [], constraints)
        self.assertNotIn("reserve", public_product(product))

    def test_unknown_constraints_fail_instead_of_being_silently_ignored(self):
        with self.assertRaises(HarnessError):
            parse_query(json.dumps({"action": "search", "constraints": {"category": "bookshelf", "waterproof": True}, "reason": "Need waterproof"}))
        with self.assertRaises(HarnessError):
            parse_query(json.dumps({"action": "search", "constraints": {"category": None}, "reason": "Unclear"}))
        answer = {"action": "clarify", "question": "How tall is your desk in centimeters?", "reason": "Unknown height"}
        self.assertEqual(parse_query(json.dumps(answer)), answer)

    def test_harness_receives_only_candidates_not_the_whole_catalog(self):
        calls = []
        def fake_decide(prompt, context, validate, **kwargs):
            calls.append((prompt, context))
            if prompt == "catalog_query.txt":
                answer = {"action": "search", "constraints": {"category": "lamp", "material": "metal", "max_height_cm": 55}, "reason": "Desk lighting"}
            else:
                product = context["candidates"][0]
                answer = {"action": "select", "product_id": product["id"], "price": 95, "reason": "Fits lighting requirement"}
            return validate(json.dumps(answer))
        with patch("catalog.decide", side_effect=fake_decide):
            result = select_product("Metal reading light no taller than 55 cm", self.catalog)
        self.assertEqual(result["action"], "select")
        self.assertNotIn("candidates", calls[0][1])
        self.assertEqual(calls[1][1]["total"], 4000)
        self.assertEqual(len(calls[1][1]["candidates"]), 8)
        self.assertLess(len(json.dumps(calls[1][1])), 7000)


class CatalogServerTests(unittest.TestCase):
    def setUp(self):
        self.product = Catalog.from_config({}).search({"category": "bookshelf", "material": "wood", "min_capacity_books": 40, "max_height_cm": 75})["candidates"][0]

    def test_quote_and_receipt_preserve_the_same_suitable_product_and_hide_reserve(self):
        selected = {"action": "select", "product": self.product, "price": 180, "reason": "Fits"}
        accepted = {"action": "accept", "price": 150, "reason": "Agreement"}
        with patch("catalog_seller.select_product", return_value=selected), patch("catalog_seller.seller_decision", return_value=accepted) as decision:
            with TestClient(build(DEFAULT)) as client:
                first = task(send(client, "Wooden storage for 40 books no taller than 75cm"))
                quote = first["status"]["message"]["parts"][1]["data"]
                self.assertEqual(quote, {"product": public_product(self.product), "ask": 180})
                final = task(send(client, "150", first["id"]))
                receipt = final["artifacts"][0]["parts"][0]["data"]
                self.assertEqual(final["status"]["state"], "TASK_STATE_COMPLETED")
                self.assertEqual(receipt["product"], quote["product"])
                self.assertEqual(receipt["item"], self.product["id"])
                self.assertEqual(receipt["price"], 150)
                self.assertNotIn("reserve", json.dumps(receipt))
                self.assertIn("error", send(client, "160", first["id"]))
        self.assertEqual(decision.call_args.kwargs["reserve"], self.product["reserve"])

    def test_unclear_description_keeps_task_open_until_clarified(self):
        replies = [{"action": "clarify", "question": "How tall is your desk in centimeters?", "reason": "Unknown height"},
                   {"action": "select", "product": self.product, "price": 180, "reason": "Fits"}]
        with patch("catalog_seller.select_product", side_effect=replies) as select:
            with TestClient(build(DEFAULT)) as client:
                first = task(send(client, "Wooden storage for 40 books, not taller than my desk"))
                self.assertEqual(first["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
                self.assertEqual(len(first["status"]["message"]["parts"]), 1)
                second = task(send(client, "The desk is 75 cm tall", first["id"]))
                self.assertEqual(second["id"], first["id"])
                self.assertEqual(second["status"]["message"]["parts"][1]["data"]["product"]["id"], self.product["id"])
        self.assertEqual(len(select.call_args.args[2]), 2)

    def test_harness_failure_does_not_fabricate_a_selection_or_price(self):
        with patch("catalog_seller.select_product", side_effect=HarnessError("invalid selection")):
            with TestClient(build(DEFAULT)) as client:
                failed = task(send(client, "Wooden storage for 40 books under 75 cm"))
                self.assertEqual(failed["status"]["state"], "TASK_STATE_FAILED")
                self.assertFalse(failed.get("artifacts"))


class CatalogCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_during_catalog_inference_cannot_publish_a_late_quote(self):
        seller = CatalogSeller(DEFAULT)
        entered, release = threading.Event(), threading.Event()
        def slow(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test worker not released")
            return {"action": "clarify", "question": "What height?", "reason": "Ambiguous"}
        task_object = SimpleNamespace(id="catalog-cancel", context_id="catalog-context")
        context = SimpleNamespace(current_task=task_object, message=Message(parts=[new_text_part("something wooden")]))
        execute_up = Mock(requires_input=AsyncMock(), failed=AsyncMock())
        cancel_up = Mock(cancel=AsyncMock())
        with patch("catalog_seller.select_product", side_effect=slow), patch("catalog_seller.TaskUpdater", side_effect=[execute_up, cancel_up]):
            running = asyncio.create_task(seller.execute(context, object()))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                await seller.cancel(context, object())
            finally:
                release.set()
            await asyncio.wait_for(running, 3)
        cancel_up.cancel.assert_awaited_once()
        execute_up.requires_input.assert_not_awaited()
        self.assertNotIn(task_object.id, seller.open)
        self.assertNotIn(task_object.id, seller.pending)


if __name__ == "__main__":
    unittest.main()
