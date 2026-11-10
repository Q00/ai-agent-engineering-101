"""Cheap food discovery never supplies freshness or purchase authorization."""
from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
import time
import unittest
from unittest.mock import patch

import buyer_public_prices as public
from buyer_fast_selection import exploration_turn
from buyer_policy import checkout_policy
from market_buyer import choose_shop, alternative_shop
import test_buyer_public_prices as fixtures


class FoodDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PublicPriceTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.order.update(kind="food", use_within_hours=12,
                            order=next(f"food-{i}" for i in range(100)
                                       if not exploration_turn(f"food-{i}")))
        self.f.catalog[fixtures.SKU]["kind"] = "food"
        self.rows = [self.f.row(i, price=30000, grade="C", warranty_hours=0) for i in (1, 2)]
        self.f.write(self.rows)

    def read(self):
        f = self.f
        return public.read_food_discovery(f.md, shops=f.shops, catalog=f.catalog,
            order=f.order, budget=100000, config=f.config, now=f.local)

    def select(self, *, model=None):
        f = self.f
        state, output = {}, StringIO()
        hints = self.read()
        with patch("market_buyer.read_food_discovery", return_value=hints), \
                patch("market_buyer.decide", side_effect=model or AssertionError("No selection model")), \
                redirect_stdout(output):
            selected, reason = choose_shop(f.shops, [], item="food", budget=100000,
                deadline=time.monotonic()+30, policy=f.config, order=f.order,
                catalog=f.catalog, market_dir=f.md, selection_state=state)
        return selected, reason, state, output.getvalue()

    def test_public_food_hint_is_not_a_comparable_or_freshness_proof(self):
        hint = self.read()[("shop", fixtures.SKU)]["groups"][0]
        self.assertEqual(hint["source"], "public_food_discovery")
        self.assertIs(hint["freshness_verified"], False)
        self.assertNotIn("minimum_hours_left", hint)
        self.assertEqual(self.f.read(), {})  # Original public comparables still exclude food.
        selected, reason, state, logs = self.select()
        self.assertEqual(selected["selected_sku"], fixtures.SKU)
        self.assertEqual(selected["price_reference"], [])
        self.assertEqual(state["values"], {})
        self.assertIn("UNKNOWN", reason)
        self.assertIn("food_bargain_scan", logs)
        self.assertEqual(checkout_policy(self.f.order, self.f.config)["min_hours_left"], 13)
        self.assertNotIn("receipt", selected)

    def test_scans_beyond_first_six_shops_and_first_five_offers(self):
        f = self.f
        f.shops = [{"name": f"other{i}", "url": f"https://other{i}.invalid",
                    "offers": [{"sku": fixtures.SKU, "qty": 1}]} for i in range(25)] + f.shops
        f.shops[-1]["offers"] = [{"sku": f"F{i}", "qty": 1} for i in range(10)] + f.shops[-1]["offers"]
        for i in range(10):
            f.catalog[f"F{i}"] = {"kind": "food", "list_price": 100000}
            f.order["acceptable"].append(f"F{i}")
        selected, _, state, _ = self.select()
        self.assertEqual((selected["name"], selected["selected_sku"]), ("shop", fixtures.SKU))
        self.assertLessEqual(len(state["candidates"]), 6)
        self.assertTrue(all(len(c["offers"]) <= 5 for c in state["candidates"]))

    def test_stale_conflicting_thin_unhonest_wrong_order_and_sold_out_evidence(self):
        variants = [self.rows[:1], [self.rows[0], {**self.rows[1], "buyer": "b1"}],
                    [*self.rows, {**self.rows[0], "price": 1}],
                    [{**r, "ts": fixtures.EPOCH-901} for r in self.rows],
                    [{**r, "claim_honest": False} for r in self.rows],
                    [{**r, "round": 2} for r in self.rows],
                    [{**r, "sku": "not-allowed"} for r in self.rows]]
        for rows in variants:
            with self.subTest(rows=rows):
                self.f.write(rows)
                self.assertEqual(self.read(), {})
        self.f.write(self.rows, updated_at=self.f.local-901)
        self.assertEqual(self.read(), {})
        self.f.write(self.rows)
        self.f.shops[0]["offers"] = [{"sku": "unobserved", "qty": 1}]
        self.f.catalog["unobserved"] = {"kind": "food", "list_price": 100000}
        def model(_, context, validate, **kw):
            return validate(json.dumps({"name": "shop", "sku": "unobserved", "reason": "explore"}))
        self.assertEqual(self.select(model=model)[0]["selected_sku"], "unobserved")

    def test_expensive_or_exploration_orders_keep_one_model_comparison(self):
        f = self.f
        for explore in (False, True):
            with self.subTest(explore=explore):
                if explore:
                    f.order["order"] = next(f"e{i}" for i in range(100) if exploration_turn(f"e{i}"))
                    f.write(self.rows)
                else:
                    f.write([{**r, "price": 60001} for r in self.rows])
                calls = []
                def model(_, context, validate, **kw):
                    calls.append(context)
                    return validate(json.dumps({"name": "shop", "sku": fixtures.SKU, "reason": "compare"}))
                selected, _, _, _ = self.select(model=model)
                self.assertEqual(len(calls), 1)
                self.assertEqual(selected["price_reference"], [])

    def test_discount_ratio_wins_over_grade_and_retry_does_not_receive_hint_as_comparable(self):
        f = self.f
        other = {"name": "other", "url": "https://other.invalid",
                 "offers": [{"sku": fixtures.SKU, "qty": 1}]}
        f.shops.append(other)
        f.write(self.rows + [f.row(i, shop=other["url"], price=45000, warranty_hours=0)
                            for i in (3, 4)])
        selected, _, state, _ = self.select()
        self.assertEqual(selected["name"], "shop")  # Cheap C before dearer A.
        retry = alternative_shop(state, selected, f.order)
        self.assertEqual(retry["name"], "other")
        self.assertEqual(retry["price_reference"], [])

    def test_missing_cache_and_registry_injected_hints_do_not_create_discovery(self):
        self.f.shops[0]["offers"][0]["price_history"] = [{
            "source": "public_food_discovery", "median_price": 1, "minimum_hours_left": 9999}]
        (self.f.md / "market-evidence.json").unlink()
        self.assertEqual(self.read(), {})


if __name__ == "__main__":
    unittest.main()
