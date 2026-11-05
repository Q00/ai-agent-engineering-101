"""A food unit goes through the same shop checks: disclosed, never sold expired, never below the floor, no warranty."""

import json
import time
import unittest

import r3_policy
import seller_r3
from test_seller_fallback import Fixture

SHELF = 100


class FoodSale(Fixture):
    def setUp(self):
        super().setUp()
        self.food = {"unit": "u1", "sku": "G1", "name": "fixture", "kind": "food", "grade": "A", "cost": 100, "list_price": 200,
                     "supplied_at": time.time(), "expires_at": time.time() + SHELF * 3600 * .9, "cert": {"x": 1}}
        self.inventory(self.food)
        (self.root / "catalog.json").write_text(json.dumps({"G1": {"kind": "food", "list_price": 200, "shelf_hours": SHELF}}), encoding="utf8")

    async def run_opening(self):
        await self.send("buy G1")
        self.assertEqual(self.last_ask(), 200)
        text = str(self.updater.requires_input.await_args.args[0])
        self.assertIn("Inventory condition: A", text)
        self.assertIn("Expires at", text)

    async def test_opening_discloses_expiry(self):
        await self.run_opening()

    async def test_expired_unit_is_refused_at_every_step(self):
        self.inventory({**self.food, "expires_at": time.time() - 1})
        await self.send("buy G1")
        self.updater.reject.assert_awaited_once()
        self.signer.assert_not_called()

    async def test_expiry_while_negotiating_is_refused(self):
        await self.send("buy G1")
        self.inventory({**self.food, "expires_at": time.time() - 1})
        await self.send("200")
        self.updater.reject.assert_awaited_once()
        self.signer.assert_not_called()

    async def test_expired_grade_is_not_offered(self):
        self.inventory({**self.food, "grade": "expired"})
        await self.send("buy G1")
        self.updater.reject.assert_awaited_once()

    async def test_offers_below_the_floor_are_not_signed(self):
        await self.send("buy G1", "99", "99", "99")
        self.signer.assert_not_called()
        self.assertGreaterEqual(self.last_ask(), 100)

    async def test_a_fair_offer_signs_with_the_grade_and_no_warranty(self):
        await self.send("buy G1", "200")
        receipt = self.signer.call_args.args[0]
        self.assertEqual((receipt["price"], receipt["claim"], receipt["warranty_hours"]), (200, "A", 0))

    def test_terms_move_to_clearance_as_shelf_life_falls(self):
        catalog = {"G1": {"shelf_hours": SHELF}}
        now = time.time()
        for left, stage in ((.5, 0), (.4, 0), (.15, 0), (.05, 1)):
            with self.subTest(left=left):
                terms = r3_policy.selling_terms({**self.food, "expires_at": now + SHELF * 3600 * left}, catalog, now=now)
                self.assertEqual(terms["discount_stage"], stage)
        self.assertEqual(r3_policy.selling_terms({**self.food, "expires_at": now + SHELF * 3600 * .5}, catalog, now=now)["floor"], 100)
        with self.assertRaises(ValueError):
            r3_policy.selling_terms({**self.food, "expires_at": now - 1}, catalog, now=now)


class FoodWarranty(unittest.TestCase):
    def test_food_is_never_enrolled_in_the_warranty_trial(self):
        import inspect
        import warranty_trial
        self.assertIn('unit.get("kind") != "goods"', inspect.getsource(warranty_trial.assignment))


if __name__ == "__main__":
    unittest.main()
