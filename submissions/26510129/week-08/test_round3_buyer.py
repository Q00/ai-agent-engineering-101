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

    def test_goods_acceptance_rejects_grade_c_but_needs_no_certificate(self):
        accept = market_buyer.accept_constraints(self.order)
        self.assertEqual(accept, {"grades": ["A", "B"]})

    def test_request_skips_skus_the_shop_already_turned_down(self):
        shop = {"offers": [{"sku": "cheap", "qty": 4}, {"sku": "valuable", "qty": 1}]}
        self.assertEqual("cheap", market_buyer.choose_request(shop, self.catalog, exclude={"valuable"})["sku"])
        self.assertIsNone(market_buyer.choose_request(shop, self.catalog, exclude={"valuable", "cheap"}))

    def test_food_unit_grade_decides_before_negotiating(self):
        self.assertTrue(market_buyer.food_unit_ok("A", 600, 360))     # 480 h at least
        self.assertFalse(market_buyer.food_unit_ok("B", 600, 360))    # 270 h at least
        self.assertFalse(market_buyer.food_unit_ok("C", 600, 1))
        self.assertTrue(market_buyer.food_unit_ok(None, 600, 360))    # unknown grade: let the kit's check decide
        self.assertTrue(market_buyer.food_unit_ok("C", None, 360))
        task = {"status": {"message": {"metadata": {"ask": 10}, "parts": [{"text": "kimchi (F1), certified grade B: asking 10."}]}}}
        self.assertEqual("B", market_buyer.unit_grade(task))
        task["status"]["message"]["metadata"]["grade"] = "A"
        self.assertEqual("A", market_buyer.unit_grade(task))
        task["status"]["message"]["parts"][0]["text"] = "apples [G1], grade B, about 34 h of shelf life left. Send a price."
        self.assertEqual(34.0, market_buyer.unit_hours_left(task))
        self.assertIsNone(market_buyer.unit_hours_left({"status": {"message": {"parts": [{"text": "asking 10"}]}}}))

    def test_a_refused_payment_counts_against_the_shop(self):
        shop = {"name": "s", "reviews": []}
        fine = [{"shop": "s", "item": "x", "outcome": "deal", "price": 100, "honest": True}]
        refused = fine + [{"shop": "s", "item": "x", "outcome": "unpaid", "price": 100, "unpaid_reason": "grade C"}]
        self.assertGreater(buyer_policy.evidence(shop, fine, "x", 100)["integrity"],
                           buyer_policy.evidence(shop, refused, "x", 100)["integrity"])

    def test_sku_reference_follows_the_market_below_the_list_cap(self):
        stats = {"skus": {"valuable": {"n": 5, "median": 150}}, "categories": {}}
        self.assertEqual(142, market_buyer.sku_reference("valuable", self.catalog, 999, stats))   # 150 x 0.95
        stats["skus"]["valuable"]["median"] = 195
        self.assertEqual(180, market_buyer.sku_reference("valuable", self.catalog, 999, stats))   # capped at 0.9 x list

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

    def test_quick_offer_takes_a_good_ask_or_a_stated_floor_inside_the_limit(self):
        self.assertEqual((186000, "the shop's stated lowest"), market_buyer.quick_offer(200000, 186000, 180000, 250000))
        self.assertEqual((170000, "the ask"), market_buyer.quick_offer(170000, None, 180000, 250000))
        self.assertEqual((None, None), market_buyer.quick_offer(200000, None, 180000, 250000))     # ask above reference: bargain
        self.assertEqual((None, None), market_buyer.quick_offer(200000, 195000, 180000, 250000))   # floor > reference x 1.05
        self.assertEqual((None, None), market_buyer.quick_offer(170000, None, 180000, 160000))     # above my limit
        self.assertEqual(186000, market_buyer.stated_floor("No deal. Lowest for this unit: 186000. receipt=Ed1"))
        self.assertEqual(1740309, market_buyer.stated_floor("lowest I can go is 1,740,309"))
        self.assertIsNone(market_buyer.stated_floor("Counteroffer: 186413."))
        self.assertEqual((200000, "the ask"), market_buyer.quick_offer(200000, None, 185000, 250000, slack=1.10))
        self.assertTrue(market_buyer.regulars_prompt("REGULARS: bought here before? reply 'buyer: <your market name>' and VIP -3% comes off"))
        self.assertFalse(market_buyer.regulars_prompt("lamp: asking 115. Send your offer as a number."))

    def test_a_receipt_may_name_any_acceptable_sku_or_a_sku_of_the_asked_category(self):
        catalog = {"G1": {"category": "home>bed"}, "G2": {"category": "home>bed"}, "K1": {"category": "food>kimchi"}}
        self.assertTrue(market_buyer.receipt_names_the_order("G1", "G1"))
        self.assertTrue(market_buyer.receipt_names_the_order("G2", "G1", acceptable={"G1", "G2"}))      # substituted SKU
        self.assertTrue(market_buyer.receipt_names_the_order("G1", "home>bed", catalog=catalog))         # asked by category
        self.assertTrue(market_buyer.receipt_names_the_order("G1", "bed", catalog=catalog))
        self.assertFalse(market_buyer.receipt_names_the_order("K1", "home>bed", acceptable={"G1"}, catalog=catalog))
        _, _, honest = market_buyer.review("home>bed", "deal", 100, {"item": "G1", "price": 100}, 90, catalog=catalog)
        self.assertTrue(honest)

    def test_one_mismatch_among_many_deals_does_not_blacklist_but_steering_does(self):
        good = [{"shop": "big", "outcome": "deal", "honest": True}] * 50 + [{"shop": "big", "outcome": "deal", "honest": False}]
        self.assertEqual(set(), buyer_policy.blacklisted_names(good))
        twice = good + [{"shop": "big", "outcome": "deal", "honest": False}] * 5           # 6 of 56: over a tenth
        self.assertEqual({"big"}, buyer_policy.blacklisted_names(twice))
        small = [{"shop": "s", "outcome": "deal", "honest": False}] * 2 + [{"shop": "s", "outcome": "deal", "honest": True}] * 3
        self.assertEqual({"s"}, buyer_policy.blacklisted_names(small))
        self.assertEqual({"m"}, buyer_policy.blacklisted_names([{"shop": "m", "outcome": "walked_away", "manipulative": True}]))

    def test_a_shop_that_answers_nothing_four_times_is_cooled_off(self):
        mem = [{"shop": "x", "outcome": "no_stock"}] * 3 + [{"shop": "x", "outcome": "stale"}]
        self.assertTrue(market_buyer.cooling_off(mem, "x"))
        self.assertFalse(market_buyer.cooling_off(mem[:3], "x"))                              # not yet four
        self.assertFalse(market_buyer.cooling_off(mem + [{"shop": "x", "outcome": "deal"}], "x"))   # answered again

    def test_closing_gap_pays_the_ask_when_my_offer_is_within_two_percent(self):
        self.assertTrue(market_buyer.closing_gap(404800, 405000, 441000, 380000, 1.05))
        self.assertFalse(market_buyer.closing_gap(385000, 405000, 441000, 380000, 1.05))     # 5% apart: keep bargaining
        self.assertFalse(market_buyer.closing_gap(404800, 405000, 400000, 380000, 1.05))     # ask above my limit
        self.assertFalse(market_buyer.closing_gap(404800, 405000, 441000, 300000, 1.05))     # ask far above the reference

    def test_regime_follows_category_scarcity(self):
        mem = [{"item": "c", "outcome": "no_stock"}] * 8 + [{"item": "c", "outcome": "deal"}] * 2
        self.assertEqual(0.8, market_buyer.scarcity(mem, "c"))
        self.assertIsNone(market_buyer.scarcity(mem[:5], "c"))
        self.assertEqual((1.10, 0.10, "scarce"), market_buyer.regime(0.8, 0.15))
        self.assertEqual((1.00, 0.20, "plentiful"), market_buyer.regime(0.1, 0.15))
        self.assertEqual((1.05, 0.15, "normal"), market_buyer.regime(0.45, 0.15))
        self.assertEqual((1.05, 0.15, "unknown"), market_buyer.regime(None, 0.15))

    def test_listed_units_raise_a_shop_a_little(self):
        memory = []
        one = {"name": "one", "reviews": [], "offers": [{"sku": "s", "qty": 1}]}
        five = {"name": "five", "reviews": [], "offers": [{"sku": "s", "qty": 5}]}
        ranked = buyer_policy.rank_shops([one, five], memory, "x", 100)
        self.assertEqual(["five", "one"], [shop["name"] for shop, _ in ranked])
        self.assertEqual(5, ranked[0][1]["available"])

    def test_sku_reference_is_90_percent_of_list_price(self):
        self.assertEqual(180, market_buyer.sku_reference("valuable", self.catalog, 999))
        self.assertEqual(999, market_buyer.sku_reference("unknown", self.catalog, 999))


if __name__ == "__main__":
    unittest.main()
