"""Affordable observed goods keep bounded pool space without weaker gates."""
from copy import deepcopy
import time
import unittest
from unittest.mock import patch

from adaptive_stock import candidates
from shop_policy import policy_at

CHEAP = tuple(f"G000001{i:02x}" for i in range(4))
EXPENSIVE = tuple(f"G000002{i:02x}" for i in range(14))
FOOD = tuple(f"F000001{i:02x}" for i in range(3))
PROVEN = {"mature": 3, "net_estimate": 100000, "speed_score": 1, "unknown": 0, "refunded": 0}


class TurnoverCandidateTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.cfg = policy_at()["restock"]
        self.rules = self.cfg["adaptive_goods"]
        self.rules["turnover_goods"] = {"enabled": True, "max_candidates": 4,
                                         "priority_units_per_run": 2, "max_list_price": 300000}
        self.rules["food_pilots"]["max_candidates"] = 2
        self.inv = {"round": 3, "start": 5000000, "balance": 11695754, "injected": 0,
                    "surge": [], "units": []}
        self.catalog, self.metrics = {}, {}
        self.public = {"schema": 1, "round": 3, "updated_at": self.now, "events": [],
            "market_stock": {"round": 3, "observed_at": self.now, "by_sku": {},
                "shop_names_by_url": {f"https://seller-{i}.invalid": f"registered-{i}" for i in range(3)}}}
        for sku in CHEAP: self.add_product(sku, 100000)
        for sku in EXPENSIVE: self.add_product(sku, 900000)
        for sku in FOOD: self.add_product(sku, 20000, "food")
        for target in ("harness.decide", "urllib.request.urlopen"):
            guard = patch(target, side_effect=AssertionError("No model/network allowed"))
            guard.start()
            self.addCleanup(guard.stop)

    def add_product(self, sku, price, kind="goods"):
        self.catalog[sku] = {"name": "Synthetic unrelated product", "category": kind, "kind": kind,
                             "list_price": price, **({"shelf_hours": 72} if kind == "food" else {})}
        self.public["market_stock"]["by_sku"][sku] = {"units": 10, "shops": 3}
        self.set_sales(sku, 5 if kind == "food" else 3, 3)

    def set_sales(self, sku, count, buyers):
        self.public["events"] = [event for event in self.public["events"] if event["sku"] != sku]
        seq = max((event["seq"] for event in self.public["events"]), default=0)
        for i in range(count):
            self.public["events"].append({"seq": seq + i + 1, "ts": self.now - 30, "kind": "settle",
                "sku": sku, "buyer": f"buyer-{i % buyers}", "shop": f"https://seller-{i % 3}.invalid",
                "price": self.catalog[sku]["list_price"] * 3 // 4, "grade": "A",
                "warranty_hours": 0 if self.catalog[sku]["kind"] == "food" else 6})

    def pool(self, **kw):
        return candidates(self.inv, self.catalog, {"goods", "food", "unrelated-category"},
                          self.metrics, self.cfg, self.now, self.public, **kw)

    def test_affordable_goods_survive_many_more_expensive_value_ranked_candidates(self):
        pool = self.pool()
        self.assertEqual(list(pool)[:4], list(CHEAP))
        self.assertEqual(len(pool), 12)
        self.assertEqual(len(set(pool)), 12)
        self.assertEqual(sum(row["product"]["kind"] == "food" for row in pool.values()), 2)
        self.assertTrue(all(pool[sku]["turnover_candidate"] and pool[sku]["evidence"]["turnover_candidate"]
                            for sku in CHEAP))
        self.assertFalse(any(row["turnover_candidate"] for row in pool.values() if row["product"]["kind"] == "food"))
        self.assertTrue(all(sku in EXPENSIVE for sku in list(pool)[4:10]))

    def test_disabled_or_absent_turnover_keeps_previous_order_exactly(self):
        self.rules["turnover_goods"]["enabled"] = False
        disabled = self.pool()
        self.rules.pop("turnover_goods")
        absent = self.pool()
        self.assertEqual(disabled, absent)
        self.assertFalse(set(disabled) & set(CHEAP))
        self.assertEqual(list(disabled)[:10], list(EXPENSIVE[:10]))
        self.assertTrue(all(not row["turnover_candidate"] for row in disabled.values()))

    def test_unused_reserved_slots_fill_with_eligible_goods_without_duplicates(self):
        for sku in FOOD: self.set_sales(sku, 0, 3)
        pool = self.pool()
        self.assertEqual(len(pool), 12)
        self.assertEqual(list(pool)[:4], list(CHEAP))
        self.assertTrue(all(row["product"]["kind"] == "goods" for row in pool.values()))
        for sku in CHEAP: self.set_sales(sku, 0, 3)
        pool = self.pool()
        self.assertEqual(list(pool), list(EXPENSIVE[:12]))
        self.assertEqual(len(set(pool)), len(pool))

    def test_proven_then_buyer_diversity_then_stock_pressure_rank_affordable_goods(self):
        self.set_sales(CHEAP[0], 6, 2)
        self.public["market_stock"]["by_sku"][CHEAP[0]]["units"] = 1
        self.set_sales(CHEAP[1], 3, 3)
        self.set_sales(CHEAP[2], 3, 2)
        self.metrics[CHEAP[2]] = deepcopy(PROVEN)
        ordered = list(self.pool())[:4]
        self.assertEqual(ordered[0], CHEAP[2])
        self.assertLess(ordered.index(CHEAP[1]), ordered.index(CHEAP[0]))
        self.public["market_stock"]["by_sku"][CHEAP[3]]["units"] = 1
        ordered = list(self.pool())[:4]
        self.assertLess(ordered.index(CHEAP[3]), ordered.index(CHEAP[1]))

    def test_price_boundary_and_arbitrary_category_do_not_invent_special_skus(self):
        self.catalog[CHEAP[0]].update(list_price=300000, category="unrelated-category")
        self.catalog[CHEAP[1]]["list_price"] = 300001
        pool = self.pool()
        self.assertIn(CHEAP[0], pool)
        self.assertTrue(pool[CHEAP[0]]["turnover_candidate"])
        self.assertFalse(pool.get(CHEAP[1], {}).get("turnover_candidate", False))
        self.assertTrue(all(not row["turnover_candidate"] for row in pool.values() if row["product"]["kind"] == "food"))

    def test_no_reserved_slot_for_missing_stale_or_insufficient_evidence(self):
        original = deepcopy(self.public)
        for variant in ("absent", "stale", "two_sales", "one_buyer", "one_named_shop"):
            self.public = deepcopy(original)
            if variant == "absent": self.set_sales(CHEAP[0], 0, 3)
            if variant == "stale": self.public["updated_at"] -= 3601
            if variant == "two_sales": self.set_sales(CHEAP[0], 2, 2)
            if variant == "one_buyer": self.set_sales(CHEAP[0], 3, 1)
            if variant == "one_named_shop":
                self.public["market_stock"]["shop_names_by_url"] = {
                    f"https://seller-{i}.invalid": "same-registered-shop" for i in range(3)}
            with self.subTest(variant=variant):
                self.assertNotIn(CHEAP[0], self.pool())

    def test_cheap_goods_still_respect_refund_uncertainty_held_and_surge_gates(self):
        sku = CHEAP[0]
        for metric in ({"unknown": 1}, {"refunded": 1}, {"mature": 1, "net_estimate": -1}):
            self.metrics[sku] = metric
            with self.subTest(metric=metric):
                self.assertNotIn(sku, self.pool())
        self.metrics = {}
        self.assertNotIn(sku, self.pool(paused={sku}))
        self.inv["surge"] = [{"sku": sku, "factor": 1.11, "units": 100}]
        self.assertNotIn(sku, self.pool())
        self.inv["surge"] = []
        self.inv["units"] = [{"unit": "held", "sku": sku, "category": "goods", "kind": "goods", "cost": 50000}]
        self.assertNotIn(sku, self.pool())
        self.metrics[sku] = deepcopy(PROVEN)
        self.assertEqual(self.pool()[sku]["cap"], 2)

    def test_cash_and_held_category_cost_can_exclude_every_affordable_good(self):
        self.inv["balance"] = 2000000
        self.assertEqual(self.pool(), {})
        self.inv["balance"] = 11695754
        self.inv["units"] = [{"unit": "concentrated", "sku": "Gffffffff", "category": "goods",
                              "kind": "goods", "cost": 4950000}]
        self.assertFalse(set(self.pool()) & set(CHEAP))
        self.assertTrue(any(row["product"]["kind"] == "food" for row in self.pool().values()))


if __name__ == "__main__":
    unittest.main()
