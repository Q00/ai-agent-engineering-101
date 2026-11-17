"""
Offline contract checks, not live data
"""

import json
import tempfile
import unittest
from pathlib import Path

from market_inventory import InventoryError, available_units, load_catalog, load_inventory, parse_inventory




def unit(unit_id, *, grade="A", expires_at=None, kind="goods"):
    return {
        "unit": unit_id, "sku": "example-sku", "name": "Example Name", "category": "example", "cost": 100, "list_price": 160, "kind": kind, "grade": grade, "expires_at": expires_at, "supplied_at": 2000
    }

class InventoryTests(unittest.TestCase):
    def test_reads_inventory_and_filters_expired_units(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            (path / "inventory.json").write_text(json.dumps({
                "round": 3, "units": [unit("fresh", expires_at=2000, kind="food"),
                                      unit("past", expires_at=999, kind="food"),
                                      unit("marked", grade="expired")],
            }), encoding="utf-8")
            units = available_units(load_inventory(path), now=1000)
            self.assertEqual([u.unit for u in units], ["fresh"])
            self.assertEqual((units[0].sku, units[0].cost, units[0].list_price),
                             ("example-sku", 100, 160))

    def test_unavailable_or_wrong_round_never_returns_fake_inventory(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(InventoryError):
                load_inventory(Path(d))
        with self.assertRaises(InventoryError):
            parse_inventory({"round": 2, "units": []})
        self.assertEqual(parse_inventory({"round": 3, "units": []}), [])
        self.assertEqual(parse_inventory({"units": []}), [])  # contract does not require round

    def test_bad_cost_or_duplicate_units_are_rejected(self):
        bad = unit("one")
        bad["cost"] = True
        with self.assertRaises(InventoryError):
            parse_inventory({"round": 3, "units": [bad]})
        with self.assertRaises(InventoryError):
            parse_inventory({"round": 3, "units": [unit("same"), unit("same")]})

    def test_catalog_prices_are_not_treated_as_stock_costs(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            (path / "catalog.json").write_text(json.dumps({
                "example-sku": {"name": "Example", "list_price": 160},
            }), encoding="utf-8")
            catalog = load_catalog(path)
            self.assertEqual(catalog["example-sku"]["list_price"], 160)
            self.assertNotIn("cost", catalog["example-sku"])
        row = unit("optional-category")
        del row["category"]
        row["cost"] = 100.5
        self.assertIsNone(parse_inventory({"round": 3, "units": [row]})[0].category)

    def test_food_missing_expiry_is_not_assumed_unlimited(self):
        row = unit("unknown-expiry", kind="food")
        del row["expires_at"]
        with self.assertRaises(InventoryError):
            parse_inventory({"round": 3, "units": [row]})
        goods = unit("goods")
        del goods["expires_at"]
        self.assertIsNone(parse_inventory({"round": 3, "units": [goods]})[0].expires_at)


if __name__ == "__main__":
    unittest.main()

