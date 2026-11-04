"""Stop rules fire on their numbers and the food table reads public rows as stated."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import analyze_shop_expansion as ase


class StopRules(unittest.TestCase):
    STOCK = {"storage_per_hour": 10, "share_older_than_24h": .1}
    BEFORE = {"hours": 24, "profit_per_hour": 1000, "broken_per_hour": 1, "deals_per_hour": 10}

    def after(self, **changes):
        return dict({"hours": 12, "profit_per_hour": 1000, "broken_per_hour": 1, "deals_per_hour": 10}, **changes)

    def test_too_soon_gives_no_verdict(self):
        self.assertFalse(ase.stop_signals(self.BEFORE, self.after(hours=11.9), self.STOCK)["verdict_possible"])
        self.assertFalse(ase.stop_signals(None, self.after(), self.STOCK)["verdict_possible"])

    def test_clean_run_has_no_signal(self):
        self.assertEqual(ase.stop_signals(self.BEFORE, self.after(), self.STOCK)["signals"], [])

    def test_each_rule_fires_at_its_number(self):
        for after, stock, signal in ((self.after(profit_per_hour=499), self.STOCK, "profit_rate_below_half_of_before"),
                                     (self.after(), dict(self.STOCK, storage_per_hour=301), "storage_above_30_percent_of_profit"),
                                     (self.after(), dict(self.STOCK, share_older_than_24h=.3), "over_30_percent_of_stock_older_than_24h"),
                                     (self.after(broken_per_hour=1.6), self.STOCK, "broken_rate_up_50_percent")):
            with self.subTest(signal=signal):
                self.assertIn(signal, ase.stop_signals(self.BEFORE, after, stock)["signals"])


class Reading(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for folder in ("logs/market-buyer", ".market"):
            (self.root / folder).mkdir(parents=True)

    def test_rates_use_the_window_edges(self):
        points = [{"observed_at": 0, "shop": {"visits": 0, "deals": 0, "profit": 0, "revenue": 0, "broken": 0}},
                  {"observed_at": 3600, "shop": {"visits": 10, "deals": 5, "profit": 100, "revenue": 500, "broken": 1}}]
        self.assertEqual(ase.rates(points, 0, 7200)["visits_per_hour"], 10)
        self.assertIsNone(ase.rates(points[:1], 0, 7200))

    def test_stock_ages_and_storage(self):
        (self.root / ".market/inventory.json").write_text(json.dumps({"storage_per_day": .24, "units": [
            {"sku": "a", "cost": 100, "supplied_at": 0, "category": "x"}, {"sku": "b", "cost": 100, "supplied_at": 90000, "category": "y"}]}), encoding="utf-8")
        result = ase.stock_now(self.root, 90000 + 3600)
        self.assertEqual((result["units"], result["skus"], result["categories"], result["storage_per_hour"], result["share_older_than_24h"]), (2, 2, 2, 2.0, .5))

    def test_food_table_counts_and_room(self):
        (self.root / ".market/catalog.json").write_text(json.dumps({"f": {"kind": "food", "category": "kimchi", "list_price": 100, "shelf_hours": 400}, "g": {"kind": "goods", "category": "bed", "list_price": 100}}), encoding="utf-8")
        (self.root / "shops.json").write_text(json.dumps({"shops": [{"name": "a", "url": "u", "stock": {"f": 12}}]}), encoding="utf-8")
        (self.root / "logs/market-buyer/20261104T000000000000Z-a.json").write_text(json.dumps({"record": {"category": "kimchi", "accept_conditions": {"min_hours_left": 102}}}), encoding="utf-8")
        rows = [{"kind": "settle", "ts": 1000 + i, "data": {"item": "f", "amount": 80, "grade": "B", "shop": "u", "buyer": f"b{i}", "expires_at": 1000 + i + 3600 * 50}} for i in range(24)]
        rows.append({"kind": "settle", "ts": 2000, "data": {"item": "g", "amount": 90, "grade": "A", "shop": "u", "buyer": "x"}})
        result = ase.food(self.root, rows, self.root / "shops.json")
        row = result["categories"][0]
        self.assertEqual((row["category"], row["settlements_24h"], row["market_stock_of_those_skus"], row["hours_of_market_stock_at_this_rate"]), ("kimchi", 24, 12, 12.0))
        self.assertEqual((row["price_per_list_median"], row["margin_per_list_estimate_median"], row["our_orders_use_within_median"], row["room_after_use_within"]), (.8, .3, 100, 80.0))
        self.assertEqual(result["food_settlements_24h"], 24)
        self.assertEqual(result["top_shops_kind_mix"]["a"], {"food": 24, "goods": 1})


if __name__ == "__main__":
    unittest.main()
