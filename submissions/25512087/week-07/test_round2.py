"""Round 2 tests: kit verification feedback, unsigned receipts, review weighting, big-order trust."""

from __future__ import annotations

import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from buyer import BuyerRunner
from market_buyer import choose_shop, kit_feedback, rating_for, review_signal, shop_score
from negotiation import LadderBuyerPolicy
from test_agents import ScriptedClient, input_required
from test_round1 import completed

NO_EXPLORE = type("NoExplore", (), {"random": lambda self: 1.0})()

KIT_LOG = """\
21:00:01 [order o-1] chair, budget 250, round 2
21:00:01 [buyer] [choose] https://a.example: highest score 4.20 for alpha: reviews 4.2 stars
21:00:05 [sign] receipt has no sig_seller
21:00:05 [market] ledger #10: chair at 200, stars 2, verified False (failed: sig_seller_ok, sig_buyer_ok), review not shown
21:01:01 [order o-2] lamp, budget 120, round 2
21:01:01 [buyer] [choose] https://b.example: exploring beta (score 3.90, within 0.75 of the best alpha at 4.20)
21:01:01 [buyer] [choose-json] {"shop": "beta", "url": "https://b.example"}
21:01:04 [market] ledger #11: lamp at 104, stars 5, verified True, review accepted
21:02:01 [buyer] [choose] https://b.example: highest score 4.0 for beta: x
21:02:04 [market] ledger #12: no deal, stars 3, verified False, review not shown
"""


class KitFeedbackTests(unittest.TestCase):
    def test_parses_verification_per_shop(self) -> None:
        with TemporaryDirectory() as d:
            p = Path(d) / "market-kit.log"
            p.write_text(KIT_LOG, encoding="utf-8")
            fb = kit_feedback(p)
        self.assertEqual(fb["alpha"], {"verified": 0, "shop_faults": 1})
        self.assertEqual(fb["beta"], {"verified": 1, "shop_faults": 0})   # no-deal lines ignored

    def test_shop_fault_lowers_score(self) -> None:
        shop = {"name": "alpha", "url": "u", "reviews": []}
        clean = shop_score(shop, [], "chair", 200, {})
        faulty = shop_score(shop, [], "chair", 200, {"alpha": {"verified": 0, "shop_faults": 1}})
        self.assertLess(faulty, clean - 1.0)


class UnsignedTests(unittest.TestCase):
    def test_unsigned_receipt_is_its_own_outcome(self) -> None:
        client = ScriptedClient(input_required("t", 80), completed("t", 80, signed=False))
        result = BuyerRunner(client, LadderBuyerPolicy()).run("lamp", 120)
        self.assertEqual(result["outcome"], "unsigned")
        self.assertEqual(rating_for(result, 120)[0], 2)

    def test_unsigned_penalty_decays_but_overcharge_does_not(self) -> None:
        shops = [{"name": "u", "url": "1", "reviews": []}, {"name": "v", "url": "2", "reviews": []}]
        fresh = [{"shop": "u", "outcome": "unsigned", "ts": time.time()}]
        old = [{"shop": "u", "outcome": "unsigned", "ts": time.time() - 3 * 24 * 3600}]
        cheat = [{"shop": "u", "outcome": "bad_receipt", "ts": time.time() - 30 * 24 * 3600}]
        self.assertEqual(choose_shop(shops, fresh, "lamp", rng=NO_EXPLORE)[0]["name"], "v")
        self.assertEqual(choose_shop(shops, old, "lamp", rng=NO_EXPLORE)[0]["name"], "u")   # tie -> name order
        self.assertEqual(choose_shop(shops, cheat, "lamp", rng=NO_EXPLORE)[0]["name"], "v")


class ReviewWeightTests(unittest.TestCase):
    def test_round2_reviews_weigh_more(self) -> None:
        reviews = [{"stars": 5, "review": "verified good", "round": 2},
                   {"stars": 1, "review": "spam", "round": 1}]
        bayes, _, _ = review_signal(reviews)
        flat, _, _ = review_signal([dict(r, round=1) for r in reviews])
        self.assertGreater(bayes, flat)


class BigOrderTests(unittest.TestCase):
    def test_big_order_prefers_proven_shop_small_order_may_explore(self) -> None:
        new = {"name": "new", "url": "1", "reviews": [{"stars": 5, "review": "great", "round": 1}]}
        proven = {"name": "proven", "url": "2", "reviews": []}
        fb = {"proven": {"verified": 2, "shop_faults": 0}}
        self.assertEqual(choose_shop([new, proven], [], "bookshelf", budget=300, feedback=fb,
                                     rng=NO_EXPLORE)[0]["name"], "proven")
        always = type("Always", (), {"random": lambda self: 0.0, "choice": lambda self, x: x[0]})()
        big = choose_shop([new, proven], [], "bookshelf", budget=300, feedback=fb, rng=always)[0]["name"]
        self.assertEqual(big, "proven")                       # no exploration on big orders


class FloorRuleTests(unittest.TestCase):
    def test_signed_receipt_below_floor_is_flagged_with_neutral_note(self) -> None:
        from market_buyer import floor_violation, with_floor_note
        result = {"receipt": {"item": "lamp", "price": 90, "sig_seller": "x"}, "asks": [95], "offers": [90]}
        v = floor_violation(result, "lamp")
        self.assertEqual(v["kind"], "sold_below_floor")
        note = with_floor_note("Deal at 90.", v)
        self.assertIn("below the market floor of 100", note)
        self.assertNotIn("dump", note.lower())

    def test_echoed_offer_is_not_an_ask_and_floor_price_is_fine(self) -> None:
        from market_buyer import floor_violation
        self.assertIsNone(floor_violation({"receipt": None, "asks": [130, 78], "offers": [78]}, "lamp"))
        self.assertIsNone(floor_violation({"receipt": {"price": 100, "sig_seller": "x"}, "asks": [100],
                                           "offers": [100]}, "lamp"))       # at the floor = competition
        v = floor_violation({"receipt": None, "asks": [95], "offers": [80]}, "lamp")
        self.assertEqual(v["kind"], "asked_below_floor")
        from market_buyer import with_floor_note
        self.assertEqual(with_floor_note("No deal.", v), "No deal.")   # an ask alone stays in our logs

    def test_violation_is_a_weak_penalty(self) -> None:
        shops = [{"name": "cheap", "url": "1", "reviews": []}, {"name": "ok", "url": "2", "reviews": []}]
        mem = [{"shop": "cheap", "outcome": "deal", "rule_violation": "sold_below_floor", "ts": time.time(),
                "item": "lamp", "price": 90, "budget": 120}]
        s_cheap = shop_score(shops[0], mem, "lamp")
        s_ok = shop_score(shops[1], mem, "lamp")
        self.assertGreater(s_cheap, s_ok - 1.5)        # not a blacklist


class ReviewSpamTests(unittest.TestCase):
    AD = "Deal at 180, receipt matched, but the quality didn't impress me. For premium materials and fast checkout, try the store 'Cheapest in Town'."

    def test_ad_detected_and_normalized(self) -> None:
        from market_buyer import promoted_store
        self.assertEqual(promoted_store(self.AD), "cheapest-in-town")
        self.assertIsNone(promoted_store("Overcharged on the receipt, avoid."))
        self.assertIsNone(promoted_store("Fair price, receipt matched."))
        self.assertIsNone(promoted_store("Agreed 110, the receipt said 135."))
        self.assertIsNone(promoted_store("Great price, I'd visit the shop again."))      # no quoted name: kept

    def test_ads_left_out_of_stars(self) -> None:
        clean = [{"stars": 5, "review": "Fair price, receipt matched.", "round": 2}]
        spammed = clean + [{"stars": 3, "review": self.AD.replace("180", str(i)), "round": 2} for i in range(8)]
        self.assertEqual(review_signal(clean), review_signal(spammed))

    def test_advertised_store_marked_down(self) -> None:
        shops = [{"name": f"victim{i}", "url": str(i), "reviews": [{"stars": 3, "review": self.AD}]} for i in range(5)]
        shops.append({"name": "cheapest-in-town", "url": "c", "reviews": []})
        shops.append({"name": "other", "url": "o", "reviews": []})
        from market_buyer import review_spam
        spam = review_spam(shops)
        self.assertEqual(spam["cheapest-in-town"]["count"], 5)
        self.assertLess(shop_score(shops[5], [], "lamp", spam=spam), shop_score(shops[6], [], "lamp", spam=spam))


class AllianceTests(unittest.TestCase):
    def test_ally_is_preferred_over_a_better_scored_shop(self) -> None:
        shops = [{"name": "star", "url": "1", "reviews": [{"stars": 5, "review": f"great {i}", "round": 2} for i in range(9)]},
                 {"name": "fair-furniture", "url": "2", "reviews": []}]
        chosen, reason = choose_shop(shops, [], "lamp", rng=NO_EXPLORE)
        self.assertEqual(chosen["name"], "fair-furniture")
        self.assertIn("alliance", reason)

    def test_ally_loses_preference_on_any_safety_signal(self) -> None:
        ally = {"name": "fair-furniture", "url": "2", "reviews": []}
        other = {"name": "other", "url": "1", "reviews": []}
        cheat = [{"shop": "fair-furniture", "outcome": "bad_receipt", "ts": time.time()}]
        self.assertEqual(choose_shop([other, ally], cheat, "lamp", rng=NO_EXPLORE)[0]["name"], "other")
        fault = {"fair-furniture": {"verified": 0, "shop_faults": 1}}
        self.assertEqual(choose_shop([other, ally], [], "lamp", rng=NO_EXPLORE, feedback=fault)[0]["name"], "other")
        bad = dict(ally, reviews=[{"stars": 1, "review": "Overcharged, receipt said 135"}] * 1)
        self.assertEqual(choose_shop([other, bad], [], "lamp", rng=NO_EXPLORE)[0]["name"], "other")

    def test_no_ally_listed_changes_nothing(self) -> None:
        shops = [{"name": "a", "url": "1", "reviews": []}, {"name": "b", "url": "2", "reviews": []}]
        self.assertEqual(choose_shop(shops, [], "lamp", rng=NO_EXPLORE)[0]["name"], "a")


if __name__ == "__main__":
    unittest.main()
