import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import seller_r3


class Round3InventorySellerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.market_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write_inventory(self, units):
        (self.market_dir / "inventory.json").write_text(json.dumps({"round": 3, "units": units}), encoding="utf-8")

    def test_exact_sku_and_grade_a_are_selected(self):
        units = [
            {"unit": "u-b", "sku": "SKU-1", "name": "rice", "category": "food>rice", "list_price": 1000,
             "kind": "goods", "cost": 500, "grade": "B", "supplied_at": 1, "expires_at": None},
            {"unit": "u-a", "sku": "SKU-1", "name": "rice", "category": "food>rice", "list_price": 1000,
             "kind": "goods", "cost": 700, "grade": "A", "supplied_at": 2, "expires_at": None},
        ]
        self.write_inventory(units)
        with patch.object(seller_r3, "MARKET_DIR", self.market_dir):
            picked = seller_r3.find_unit("I want rice (SKU-1)")
        self.assertEqual(picked["unit"], "u-a")

    def test_near_expiry_food_has_explicit_loss_cut(self):
        now = time.time()
        unit = {"sku": "FOOD-1", "kind": "food", "cost": 700, "list_price": 1000, "grade": "C",
                "expires_at": now + 1800}
        plan = seller_r3.price_plan(unit, "task", now)
        self.assertLess(plan["floor"], unit["cost"])
        self.assertLess(plan["ask"], unit["cost"])

    def test_goods_do_not_have_automatic_loss_cut(self):
        unit = {"sku": "GOOD-1", "kind": "goods", "cost": 700, "list_price": 1000, "grade": "A",
                "expires_at": None}
        plan = seller_r3.price_plan(unit, "task")
        self.assertGreater(plan["floor"], unit["cost"])

    def test_market_price_moves_the_ask_but_not_below_cost_or_above_the_cap(self):
        unit = {"sku": "GOOD-1", "kind": "goods", "cost": 700, "list_price": 1000, "grade": "A", "expires_at": None}
        plan = seller_r3.price_plan(unit, "task", market=(900.0, 5, "sku"))
        self.assertIn(plan["ask"], (873, 945))          # 0.97x on the volume arm, 1.05x on the margin arm
        self.assertEqual((900, 5, "sku"), (plan["market"], plan["market_n"], plan["market_src"]))
        self.assertEqual(721, seller_r3.price_plan(unit, "task", market=(500.0, 5, "sku"))["ask"])    # floor: cost x 1.03
        self.assertEqual(990, seller_r3.price_plan(unit, "task", market=(2000.0, 5, "sku"))["ask"])   # cap: list x 0.99
        self.assertIsNone(seller_r3.price_plan(unit, "task", market=(None, 0, "none"))["market"])

    def test_receipt_options_are_truthful_and_grade_sensitive(self):
        options = seller_r3.receipt_options({"unit": "u1", "kind": "goods", "grade": "A"})
        self.assertEqual(options, {"unit": "u1", "claim": "A", "cert": True, "warranty_hours": 6})

    def test_policy_arm_is_stable(self):
        unit = {"sku": "GOOD-1", "kind": "goods"}
        self.assertEqual(seller_r3.policy_arm(unit, "task"), seller_r3.policy_arm(unit, "task"))


if __name__ == "__main__":
    unittest.main()
