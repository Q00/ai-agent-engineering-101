import unittest

import buyer_policy


class PortfolioRouterTest(unittest.TestCase):
    def test_public_fallback_is_budget_blind_and_moves_to_reference(self):
        self.assertEqual(92, buyer_policy.public_fallback_offer(100, 120, 0))
        self.assertEqual(96, buyer_policy.public_fallback_offer(100, 120, 1))
        self.assertEqual(100, buyer_policy.public_fallback_offer(100, 120, 2))
        self.assertEqual(80, buyer_policy.public_fallback_offer(100, 80, 2))

    def test_good_reviews_beat_bad_reviews_for_unknown_shops(self):
        bad = {"name": "bad", "reviews": [{"stars": 1}, {"stars": 2}]}
        good = {"name": "good", "reviews": [{"stars": 5}, {"stars": 4}]}
        ranked = buyer_policy.rank_shops([bad, good], [], "lamp")
        self.assertEqual(ranked[0][0]["name"], "good")

    def test_local_integrity_blacklists_a_shop(self):
        shops = [{"name": "bad", "reviews": []}, {"name": "fresh", "reviews": []}]
        memory = [{"shop": "bad", "item": "lamp", "outcome": "deal", "price": 100, "honest": False}]
        ranked = buyer_policy.rank_shops(shops, memory, "lamp")
        self.assertEqual([shop["name"] for shop, _ in ranked], ["fresh"])

    def test_concentration_cap_keeps_an_alternative(self):
        shops = [{"name": "frequent", "reviews": [{"stars": 5}]}, {"name": "other", "reviews": [{"stars": 4}]}]
        memory = [
            {"shop": "frequent", "item": "chair", "outcome": "deal", "price": 180, "honest": True}
            for _ in range(buyer_policy.MAX_VISITS_PER_WINDOW)
        ]
        ranked = buyer_policy.rank_shops(shops, memory, "chair")
        self.assertEqual([shop["name"] for shop, _ in ranked], ["other"])

    def test_reference_uses_recent_honest_deal_median(self):
        memory = [
            {"shop": "s", "item": "lamp", "outcome": "deal", "price": price, "honest": True}
            for price in (100, 104, 110)
        ]
        self.assertEqual(buyer_policy.item_reference(memory, "lamp"), 104)


class MandateGuardianTest(unittest.TestCase):
    def test_early_over_budget_proposal_does_not_reveal_ceiling(self):
        guardian = buyer_policy.MandateGuardian(120, 103)
        decision = guardian.authorize(140, 0, current_ask=140)
        self.assertEqual(decision["action"], "offer")
        self.assertLess(decision["price"], 120)
        self.assertEqual(decision["decision"], "reference_substitute")

    def test_final_round_can_use_ceiling_once(self):
        guardian = buyer_policy.MandateGuardian(120, 130)
        self.assertEqual(guardian.authorize(140, 2)["price"], 120)
        self.assertEqual(guardian.authorize(140, 3)["action"], "walk_away")

    def test_checkout_never_exceeds_budget(self):
        guardian = buyer_policy.MandateGuardian(120, 103)
        self.assertTrue(guardian.checkout_ok(120))
        self.assertFalse(guardian.checkout_ok(121))


if __name__ == "__main__":
    unittest.main()
