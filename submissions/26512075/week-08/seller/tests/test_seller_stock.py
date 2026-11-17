import json
import tempfile
import unittest

from pathlib import Path
from market_inventory import InventoryError
from seller_stock import select_stock, revalidate_stock


class SellerStockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.market = Path(self.tmp.name)
        self.now = 2_000_000
        self.food = {"unit": "u1", "sku": "G12345678", "name": "milk", "category": "food>milk",
                     "kind": "food", "cost": 101.2, "list_price": 500, "grade": "A",
                     "expires_at": self.now + 10 * 3600, "supplied_at": self.now - 100}
        self.other = {**self.food, "unit": "u2", "sku": "G87654321", "cost": 40,
                      "expires_at": self.now + 5 * 3600}
        self.write([self.food, self.other])

    def write(self, units, round_number=3):
        (self.market / "inventory.json").write_text(
            json.dumps({"round": round_number, "units": units}), encoding="utf-8")

    def test_uses_exact_sku_and_actual_cost_instead_of_list_price(self):
        choice = select_stock(self.market, sku="G12345678", now=self.now)
        self.assertEqual(choice.unit.unit, "u1")
        self.assertEqual(choice.reserve, 102)
        self.assertIsNone(select_stock(self.market, sku="G1234567", now=self.now))

    def test_category_grade_and_freshness_constraints(self):
        choice = select_stock(self.market, category="food>milk", allowed_grades=["A"],
                              min_hours_left=6, now=self.now)
        self.assertEqual(choice.unit.unit, "u1")
        self.assertIsNone(select_stock(self.market, category="food>milk", allowed_grades=["B"],
                                       now=self.now))
        self.assertIsNone(select_stock(self.market, category="milk", now=self.now))
        self.assertIsNone(select_stock(self.market, sku="G12345678", min_hours_left=11, now=self.now))

    def test_expired_unit_and_multiple_candidates(self):
        self.write([{**self.food, "expires_at": self.now - 1}, self.other])
        self.assertEqual(select_stock(self.market, category="food>milk", now=self.now).unit.unit, "u2")

    def test_acceptance_rechecks_unit_and_cost_and_expiry(self):
        choice = select_stock(self.market, sku="G12345678", now=self.now)
        self.assertEqual(revalidate_stock(self.market, choice, min_hours_left=1, now=self.now).unit, "u1")
        self.write([{**self.food, "cost": 102}, self.other])
        with self.assertRaises(InventoryError):
            revalidate_stock(self.market, choice, now=self.now)
        self.write([self.other])
        with self.assertRaises(InventoryError):
            revalidate_stock(self.market, choice, now=self.now)
        self.write([self.food])
        with self.assertRaises(InventoryError):
            revalidate_stock(self.market, choice, min_hours_left=1, now=self.now + 10 * 3600)

    def test_refuses_unidentified_or_wrong_round(self):
        for wrong_round in (2, None, True, 4):
            self.write([self.food], round_number=wrong_round)
            with self.assertRaises(InventoryError):
                select_stock(self.market, sku="G12345678", now=self.now)

    def test_rejects_invalid_constraints(self):
        with self.assertRaises(ValueError):
            select_stock(self.market, now=self.now)
        with self.assertRaises(ValueError):
            select_stock(self.market, sku="G12345678", min_hours_left=float("nan"), now=self.now)
        with self.assertRaises(ValueError):
            select_stock(self.market, sku="G12345678", allowed_grades="A", now=self.now)


if __name__ == "__main__":
    unittest.main()






