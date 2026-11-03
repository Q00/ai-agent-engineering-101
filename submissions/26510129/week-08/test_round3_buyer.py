import unittest

import buyer_policy
import market_buyer


class Round3BuyerContextTest(unittest.TestCase):
    def setUp(self):
        self.catalog = {
            "cheap": {"list_price": 100, "kind": "goods"},
            "valuable": {"list_price": 200, "kind": "goods"},
            "too-high": {"list_price": 1000, "kind": "goods"},
        }
        self.order = {"acceptable": ["cheap", "valuable", "too-high"], "category": "home>bed", "kind": "goods"}

    def test_guardian_filters_without_exposing_ceiling(self):
        shops = [{"name": "s", "url": "u", "offers": [
            {"sku": "cheap", "name": "c", "qty": 1},
            {"sku": "too-high", "name": "h", "qty": 1},
        ]}]
        guardian = buyer_policy.MandateGuardian(150, 100)
        prepared = market_buyer.prepare_round3_shops(shops, self.order, self.catalog, guardian)
        self.assertEqual([offer["sku"] for offer in prepared[0]["_eligible_offers"]], ["cheap"])

    def test_scout_prefers_high_value_affordable_sku(self):
        shop = {"offers": [{"sku": "cheap", "qty": 4}, {"sku": "valuable", "qty": 1}]}
        self.assertEqual(market_buyer.choose_request(shop, self.catalog)["sku"], "valuable")

    def test_food_acceptance_requires_lifetime_certificate(self):
        accept = market_buyer.accept_constraints({"acceptable": ["f"], "kind": "food", "use_within_hours": 2.2})
        self.assertEqual(accept, {"cert": True, "min_hours_left": 3})

    def test_goods_acceptance_rejects_grade_c(self):
        accept = market_buyer.accept_constraints(self.order)
        self.assertEqual(accept, {"cert": True, "grades": ["A", "B"]})

    def test_catalog_reference_is_discounted_median(self):
        self.assertEqual(market_buyer.category_reference(self.order, self.catalog), 180)

    def test_guardian_never_pays_above_the_ask_or_the_value(self):
        guardian = buyer_policy.MandateGuardian(40000, 26010, value_cap=28900)
        self.assertEqual({"action": "offer", "decision": "capped_at_ask", "price": 26500},
                         guardian.authorize(30500, 0, current_ask=26500))
        self.assertEqual("reference_substitute", guardian.authorize(30000, 0)["decision"])   # 30000 is above the value
        self.assertEqual(28900, guardian.authorize(35000, 2)["price"])                        # final ceiling: value, not budget
        self.assertFalse(guardian.checkout_ok(28901))

    def test_food_request_prefers_a_sku_whose_shelf_life_covers_the_order(self):
        catalog = {"short": {"list_price": 300, "kind": "food", "shelf_hours": 300},
                   "tight": {"list_price": 250, "kind": "food", "shelf_hours": 400},
                   "long": {"list_price": 200, "kind": "food", "shelf_hours": 600}}
        shop = {"offers": [{"sku": "short", "qty": 1}, {"sku": "tight", "qty": 1}, {"sku": "long", "qty": 1}]}
        order = {"kind": "food", "use_within_hours": 360}
        self.assertEqual("long", market_buyer.choose_request(shop, catalog, order)["sku"])   # 600 x 0.8 covers 360; 400 x 0.8 does not
        order["use_within_hours"] = 500
        self.assertEqual("long", market_buyer.choose_request(shop, catalog, order)["sku"])   # the only feasible one
        self.assertEqual("short", market_buyer.choose_request(shop, catalog)["sku"])         # no order: list price wins

    def test_sku_reference_is_90_percent_of_list_price(self):
        self.assertEqual(180, market_buyer.sku_reference("valuable", self.catalog, 999))
        self.assertEqual(999, market_buyer.sku_reference("unknown", self.catalog, 999))


if __name__ == "__main__":
    unittest.main()
