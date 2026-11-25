"""Quote scheduling at its public prefetch boundary; no market or model calls."""
import json
import contextlib
import io
import sys
import os
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

import market_buyer as mb


class PhaseOne(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(mb.time, "time", return_value=1794020400)
        p.start()
        self.addCleanup(p.stop)


class NoStockITT(PhaseOne):
    def test_no_stock_order_still_records_its_assigned_arm(self):
        with tempfile.TemporaryDirectory() as md:
            for name, data in [("shops.json", []), ("order.json", {"order": "o-0", "kind": "food", "acceptable": ["G1"]})]:
                with open(os.path.join(md, name), "w") as f:
                    json.dump(data, f)
            argv = ["market_buyer", "--shops", os.path.join(md, "shops.json"), "--order",
                    os.path.join(md, "order.json"), "--item", "food", "--budget", "100"]
            with mock.patch.object(sys, "argv", argv), mock.patch.object(mb, "MEMORY", os.path.join(md, "mem.json")), \
                    mock.patch.dict(os.environ, {"BUYER_QUOTE_YIELD": "1"}), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mb.main(), 0)
            with open(os.path.join(md, "measure", "orders.jsonl")) as f:
                r = json.loads(f.readline())
            self.assertTrue(r["food_quote_yield"])
            self.assertTrue(r["quote_attempts_complete"])
            self.assertEqual(r["quotes"], [])
            self.assertEqual(r["state"], "NO_SHOP_IN_STOCK")


class ExperimentArms(PhaseOne):
    def test_stable_half_split_and_missing_id_is_control(self):
        self.assertTrue(mb.quote_yield_arm("o-0", {}))
        self.assertFalse(mb.quote_yield_arm("o-2", {}))
        self.assertFalse(mb.quote_yield_arm(None, {}))
        self.assertFalse(mb.quote_yield_arm("o-0", {"BUYER_QUOTE_YIELD": "0"}))
        self.assertFalse(mb.quote_yield_arm("o-0", {}, now=mb.QUOTE_YIELD_END))


class AttemptRecording(PhaseOne):
    def test_network_failure_is_a_zero_quote_in_the_order_record(self):
        s = {"name": "failed", "url": "https://example.invalid", "reviews": [],
             "usable": [{"sku": "G1", "name": "food", "list_price": 100}]}
        got = {}
        with mock.patch.object(mb, "serves_acp", side_effect=TimeoutError("test network boundary")):
            threads = mb.start_quotes([s], {"kind": "food"}, 100, lambda sku: 0, got, mb.quote_ctl())
            for th in threads.values():
                th.join(2)
        r = mb.order_record({"order": "o-0", "kind": "food"}, SimpleNamespace(budget=100, item="c"),
                            [s], got, "failed", "", s, "", s["usable"][0], 70, None,
                            {"state": "ERROR"}, None, {}, [], [], False, "shop", {}, {})
        self.assertTrue(r["quote_attempts_complete"])
        self.assertEqual(len(r["quotes"]), 1)
        self.assertEqual(r["quotes"][0]["shop"], "failed")
        self.assertFalse(r["quotes"][0]["fit"])
        self.assertIsNone(r["quotes"][0]["ask"])

    def test_legacy_response_only_history_does_not_train_attempt_yield(self):
        now = time.time()
        rows = [{"ts": now - 10, "category": "c", "kind": "food",
                 "quotes": [{"shop": "a", "ask": 50, "list_price": 100, "fit": True}]} for _ in range(10)]
        with tempfile.TemporaryDirectory() as md:
            os.mkdir(os.path.join(md, "measure"))
            with open(os.path.join(md, "measure", "orders.jsonl"), "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
            self.assertEqual(mb.recent_quote_yields(md, "c", now=now), {})


class FoodQuoteYield(PhaseOne):
    def test_food_prefetch_values_valid_saved_share_not_review_count(self):
        now = time.time()
        shops = [{"name": "cheap", "reviews": [{}], "usable": []},
                 {"name": "famous", "reviews": [{}] * 100, "usable": []},
                 {"name": "new", "reviews": [], "usable": []}]
        rows = [{"ts": now - 60 - i, "category": "c", "kind": "food", "quote_attempts_complete": True, "budget": 1000,
                 "quotes": [{"shop": "cheap", "ask": 50, "list_price": 100, "fit": True},
                            {"shop": "famous", "ask": 69, "list_price": 100, "fit": True}]}
                for i in range(3)]
        with tempfile.TemporaryDirectory() as md:
            os.mkdir(os.path.join(md, "measure"))
            with open(os.path.join(md, "measure", "orders.jsonl"), "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
            with mock.patch.dict(os.environ, {"BUYER_QUOTE_YIELD": "1"}):
                early, all_shops = mb.prefetch_pool(shops, {"trades": []}, md, "c", 3, kind="food", order_id="o-0")
            self.assertEqual([s["name"] for s in all_shops], ["cheap", "new", "famous"])
            self.assertEqual([s["name"] for s in early], ["cheap", "new"])
            with mock.patch.object(mb.time, "time", return_value=mb.QUOTE_YIELD_END+1):
                _, pinned = mb.prefetch_pool(shops, {"trades": []}, md, "c", 3, kind="food",
                                             order_id="o-0", yield_arm=True)
            self.assertEqual([s["name"] for s in pinned], ["cheap", "new", "famous"])
            with mock.patch.dict(os.environ, {"BUYER_QUOTE_YIELD": "0"}):
                _, old = mb.prefetch_pool(shops, {"trades": []}, md, "c", 3, kind="food", order_id="o-0")
            self.assertEqual([s["name"] for s in old], ["famous", "new", "cheap"])
            _, goods = mb.prefetch_pool(shops, {"trades": []}, md, "c", 3, kind="goods")
            self.assertEqual([s["name"] for s in goods], ["famous", "new", "cheap"])

    def test_failures_count_and_future_other_category_and_goods_do_not_train_food(self):
        now = time.time()
        quotes = lambda fit: [{"shop": "a", "ask": 50, "list_price": 100, "fit": fit}]
        rows = [{"ts": now - 10, "category": "c", "kind": "food", "quote_attempts_complete": True, "quotes": quotes(True)} for _ in range(3)]
        rows += [{"ts": now - 5, "category": "c", "kind": "food", "quote_attempts_complete": True, "quotes": quotes(False)}]
        rows += [{"ts": now + 1, "category": "c", "kind": "food", "quotes": quotes(True)},
                 {"ts": now - 20, "category": "other", "kind": "food", "quotes": quotes(True)},
                 {"ts": now - 20, "category": "c", "kind": "goods", "quotes": quotes(True)}]
        with tempfile.TemporaryDirectory() as md:
            os.mkdir(os.path.join(md, "measure"))
            with open(os.path.join(md, "measure", "orders.jsonl"), "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
            score = mb.recent_quote_yields(md, "c", now=now)
        self.assertAlmostEqual(score["a"], 1.675 / 6)  # 3 x .5 plus prior .175, 4 attempts + 2 prior
