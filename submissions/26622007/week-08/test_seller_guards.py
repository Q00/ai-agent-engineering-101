"""The shop's counter never rises above its own last ask (메인 11/07 03:32; GPT Pro negotiation-formula.md invariant).

11/06 22:45 task 390b9f8c, Gbec2b68f (reserve 2,467,210, opened 2,657,800): round 4 the buyer offered 2,450,000 under the
reserve, GUARD countered at the reserve 2,467,210, under the round's floor 2,486,269; round 5 the buyer offered exactly that
ask and SCHEDULE ("accept under the floor -> counter at the floor") asked 2,486,269, more than we had just asked."""
import unittest
from unittest import mock

import seller

CASE = {"ask": 2486269, "reserve": 2467210, "open": 2657800, "rounds": 4, "kind": "goods"}


def guards(deal, action, price, offer, floor):
    with mock.patch.object(seller, "log"):
        return seller.price_guards(deal, action, price, offer, floor)


class TurnFloorTest(unittest.TestCase):
    def test_the_floor_never_sits_above_our_current_ask(self):
        deal = dict(CASE, ask=2467210, rounds=5)
        with mock.patch("shop_memory.load_pricing", return_value={"concession": [0.3, 0.2, 0.1]}):
            self.assertEqual(seller.turn_floor(deal), 2467210)
            self.assertEqual(seller.turn_floor(dict(deal, ask=2657800, rounds=1)), 2467210 + round(0.3 * 190590))


class PriceGuardsTest(unittest.TestCase):
    def test_the_22_45_case_takes_our_own_ask_instead_of_raising_it(self):
        deal = dict(CASE, ask=2467210, rounds=5)
        floor = min(2486269, deal["ask"])   # what turn_floor gives here
        self.assertEqual(guards(deal, "accept", 2467210, 2467210, floor), ("accept", 2467210))

    def test_an_offer_under_the_reserve_is_countered_at_the_floor_not_the_reserve(self):
        self.assertEqual(guards(dict(CASE), "accept", 2450000, 2450000, 2486269), ("counter", 2486269))

    def test_no_path_counters_above_our_last_ask(self):
        deal = dict(CASE, ask=2500000, rounds=3)
        floor = 2486269                      # what turn_floor gives: at most the ask
        for action, price, offer in [("counter", 2600000, 2400000), ("accept", 2460000, 2460000),
                                     ("accept", 2468000, 2468000), ("counter", 2300000, 2400000),
                                     ("reject", 0, 2400000), ("accept", 2490000, 2490000)]:
            a, p = guards(dict(deal), action, price, offer, floor)
            if a == "counter":
                self.assertLessEqual(p, deal["ask"], (action, price, offer))
                self.assertGreaterEqual(p, deal["reserve"])
            if a == "accept":                # the shop sells at the offer
                self.assertGreaterEqual(offer, floor, (action, price, offer))

    def test_never_under_the_reserve(self):
        self.assertEqual(guards(dict(CASE, ask=2600000), "counter", 100, 2000000, 2486269), ("counter", 2486269))
        self.assertEqual(guards(dict(CASE, ask=2600000), "accept", 2000000, 2000000, 2400000)[0], "counter")

    def test_the_round_limit_still_ends_the_talk(self):
        self.assertEqual(guards(dict(CASE, ask=2600000, rounds=seller.MAX_ROUNDS), "counter", 2550000, 2400000, 2486269)[0],
                         "reject")


if __name__ == "__main__":
    unittest.main()
