from pathlib import Path
import tempfile
import time
import unittest

from market_state import write
from purchase_monitor import analyze, observe


class PurchaseObservation(unittest.TestCase):
    def row(self, seq=1, **data):
        return {"seq": seq, "round": 3, "kind": "settle", "ts": 100,
                "data": {"buyer": "b", "shop": "shop", "item": "A", "amount": 90,
                         "grade": "A", "warranty_hours": 0, **data}}

    def test_only_valid_recent_unique_settlements_count(self):
        row = self.row()
        rows = [None, {"data": []}, row, row, {**self.row(2), "round": 2}, {**self.row(3), "kind": "result"},
                {**self.row(4), "ts": -90000}, self.row(5, amount=float("nan")), self.row(6, price=80)]
        result = analyze(rows, {"A": {"name": "item"}}, {}, {}, 200)
        self.assertEqual(result["settled_count"], 1)
        self.assertEqual(result["buyers"][0]["observed_spend"], 90)

    def test_buyer_repeat_purchases_and_price_range(self):
        result = analyze([self.row(), self.row(2, amount=110, shop="other"), self.row(3, buyer="c", amount=100)],
                         {"A": {"name": "item", "category": "category"}}, {}, {}, 200)
        product = result["products"][0]
        self.assertEqual(product["prices"], {"count": 3, "min": 90, "median": 100, "max": 110})
        self.assertEqual(product["distinct_buyers"], 2)
        buyer = next(b for b in result["buyers"] if b["buyer"] == "b")
        self.assertTrue(buyer["top_skus"][0]["repeat_observed"])
        self.assertEqual(buyer["observed_spend"], 200)
        self.assertEqual(buyer["top_skus"][0]["shops"], ["other", "shop"])

    def test_grade_warranty_and_cost_comparisons_stay_separate(self):
        rows = [self.row(amount=120), self.row(2, grade="C", amount=30), self.row(3, warranty_hours=6, amount=150)]
        unit = {"unit": "u", "sku": "A", "name": "item", "cost": 100, "kind": "goods",
                "grade": "A", "list_price": 200, "supplied_at": 100}
        result = analyze(rows, {"A": {"list_price": 200}}, {"units": [unit]}, {}, 200)
        comparison = result["own_stock_comparison"][0]
        self.assertEqual(len(result["products"][0]["conditions"]), 3)
        self.assertEqual(comparison["same_grade_zero_warranty_prices"]["median"], 120)
        self.assertEqual(comparison["median_minus_own_cost"], 20)
        self.assertTrue(comparison["thin_sample"])

    def test_forum_demand_joins_sku_but_is_not_counted_as_purchase(self):
        forum = {"status": "ok", "observed_at": 100, "summary": {"demand_skus": [{"sku": "A", "distinct_requesters": 3}]}}
        result = analyze([self.row()], {"A": {}}, {}, forum, 200)
        self.assertEqual(result["products"][0]["forum_requesters"], 3)
        self.assertEqual(result["settled_count"], 1)

    def test_same_snapshot_is_not_logged_each_minute(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp)/".market"
            now = time.time()
            write(md/"market-intel.json", {"round": 3, "observed_at": now, "rows": []})
            first = observe(md, now=now)
            self.assertEqual(first, observe(md, now=now+60))
            self.assertEqual(len(list((Path(tmp)/"logs/purchase-monitor").glob("*.json"))), 1)
            self.assertEqual(first["model_calls"], 0)

    def test_stale_market_snapshot_is_not_reported_as_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp)/".market"
            write(md/"market-intel.json", {"round": 3, "observed_at": 100, "rows": [self.row()]})
            self.assertEqual(observe(md, now=4000)["status"], "stale_or_not_r3")


if __name__ == "__main__":
    unittest.main()
