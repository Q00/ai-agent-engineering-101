"""Tests for the rule-based shop, adaptive pricing and the Round 1 market buyer."""

from __future__ import annotations

import random
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from buyer import BuyerRunner, check_receipt
from market_buyer import choose_shop, rating_for, review_signal
from negotiation import FastSellerPolicy, LadderBuyerPolicy, find_item, parse_offer, says_accept
from pricing import AdaptivePricer
from test_agents import ScriptedClient, input_required

RESERVES = {"lamp": 100, "chair": 180, "bookshelf": 260}


def completed(task_id: str, price: int, item: str = "lamp", signed: bool = True) -> dict:
    data = {"shop": "s", "item": item, "price": price, "task": task_id}
    if signed:
        data["sig_seller"] = "c2ln"
    return {"id": task_id, "status": {"state": "TASK_STATE_COMPLETED", "message": {"parts": []}},
            "artifacts": [{"name": "receipt", "parts": [{"data": data}]}]}


class ParsingTests(unittest.TestCase):
    def test_item_aliases(self) -> None:
        self.assertEqual(find_item("Do you have a book shelf?"), "bookshelf")
        self.assertEqual(find_item("a bookcase please"), "bookshelf")
        self.assertEqual(find_item("의자 사고 싶어요"), "chair")
        self.assertIsNone(find_item("I need a sofa"))

    def test_offer_is_last_number_rounded_down(self) -> None:
        self.assertEqual(parse_offer("my budget was 300 but I offer 250"), 250)
        self.assertEqual(parse_offer("110.9"), 110)
        self.assertIsNone(parse_offer("ok deal"))

    def test_ambiguous_or_hostile_numbers_are_refused_not_misread(self) -> None:
        from negotiation import parse_price
        self.assertEqual(parse_price("185"), (185, None))
        self.assertEqual(parse_price("I offer 185."), (185, None))
        self.assertEqual(parse_price("185.5"), (185, None))
        self.assertEqual(parse_price("Counteroffer: 104.2", round_up=True), (105, None))
        for bad in ("185,000", "1,850", "185.000", "1.850.000", "-5", "999999999"):
            value, problem = parse_price(bad)
            self.assertIsNone(value, bad)
            self.assertTrue(problem, bad)
        self.assertEqual(parse_price("no number here"), (None, None))
        self.assertIsNone(parse_offer("185,000"))

    def test_accept_words_and_negation(self) -> None:
        self.assertTrue(says_accept("OK, deal"))
        self.assertTrue(says_accept("I accept"))
        self.assertFalse(says_accept("no deal"))
        self.assertFalse(says_accept("I want a bookshelf"))


class FastSellerTests(unittest.TestCase):
    def test_first_offer_under_ask_countered_then_floor_accepted(self) -> None:
        p = FastSellerPolicy()
        first = p.respond(100, 115, 100, 0)
        self.assertEqual((first.action, first.price), ("counter", 107))   # halfway to the ask
        self.assertEqual(p.respond(100, 101, 100, 0).price, 101)          # at least one above the offer
        self.assertEqual(p.respond(100, 115, 100, 1).action, "accept")    # second offer at the floor: signed
        self.assertEqual(p.respond(100, 115, 115, 0).action, "accept")    # at the ask: closes at once
        self.assertEqual(p.respond(100, 115, 130, 0).price, 130)          # receipt = the buyer's own number

    def test_counter_message_keeps_price_last_and_explains_floor(self) -> None:
        import re
        from seller import counter_message
        at_floor = counter_message(150, 180, 180, "chair")
        self.assertEqual(re.findall(r"\d+", at_floor)[-1], "180")
        self.assertIn("market floor", at_floor)
        self.assertIn("final price", at_floor)
        above = counter_message(150, 200, 180, "chair")
        self.assertNotIn("floor", above)
        self.assertEqual(re.findall(r"\d+", above)[-1], "200")

    def test_converges_to_reserve_by_third_counter(self) -> None:
        p = FastSellerPolicy()
        asks = [p.respond(260, 300, 140, r).price for r in range(3)]
        self.assertEqual(asks[-1], 260)
        self.assertTrue(all(a >= 260 for a in asks))


class FloorHaggleTests(unittest.TestCase):
    """Below-floor offers: show the final price at once, end a dead-end haggle early (no 12 x "too low")."""

    def test_floor_pricing_counters_with_reserve_at_once(self) -> None:
        p = FastSellerPolicy()
        self.assertEqual(p.respond(100, 100, 95, 0).price, 100)
        self.assertEqual(p.respond(100, 110, 95, 0).price, 102)    # one midpoint when there is room
        self.assertEqual(p.respond(100, 110, 96, 1).price, 100)    # then the final price

    def test_should_end_only_when_buyer_stops_rising(self) -> None:
        p = FastSellerPolicy()
        self.assertFalse(p.should_end(100, [95], 0))               # final price not shown yet
        self.assertFalse(p.should_end(100, [95, 97], 1))           # still climbing: let it reach 100
        self.assertTrue(p.should_end(100, [95, 95], 1))            # fixed budget below the floor
        self.assertTrue(p.should_end(100, [95, 96, 97, 98], 3))    # patience used up
        self.assertFalse(p.should_end(100, [95, 100], 1))          # at the floor: accept, not end

    def test_messages_keep_price_last(self) -> None:
        import re
        from negotiation import end_message, opening_message
        for text, want in ((opening_message("lamp", 100, 100), "100"), (opening_message("lamp", 110, 100), "110"),
                           (end_message("chair", 180), "180")):
            self.assertEqual(re.findall(r"\d+", text)[-1], want)
        self.assertIn("no haggling", opening_message("lamp", 100, 100))


class PricingTests(unittest.TestCase):
    def test_markup_moves_but_never_below_reserve(self) -> None:
        with TemporaryDirectory() as d:
            pr = AdaptivePricer(RESERVES, Path(d) / "s.json", Path(d) / "e.jsonl",
                                Path(d) / "launch.json", Path(d) / "market.json")   # never the live .market files
            start = pr.ask("lamp")
            self.assertEqual(start, 108)                         # no market scan yet: reserve x 1.08 cap
            before = pr.state["lamp"]["markup"]
            pr.deal("lamp", start, start, rounds=0)
            self.assertGreater(pr.state["lamp"]["markup"], before)   # demand is still learned
            for _ in range(20):
                pr.lost("lamp", [])
            self.assertGreaterEqual(pr.ask("lamp"), 100)
            before = pr.ask("chair")
            pr.lost("chair", [120, 150])            # budget below floor: no price change
            self.assertEqual(pr.ask("chair"), before)
            self.assertTrue((Path(d) / "e.jsonl").exists())


class LaunchTests(unittest.TestCase):
    def test_launch_sells_at_reserve_then_ramps_back(self) -> None:
        import json
        with TemporaryDirectory() as d:
            launch = Path(d) / "launch.json"
            pr = AdaptivePricer(RESERVES, Path(d) / "s.json", Path(d) / "e.jsonl", launch, Path(d) / "market.json")
            normal = pr.ask("chair")
            launch.write_text(json.dumps({"active": True, "flat": 2, "ramp": 4, "deals": 0}))
            self.assertEqual(pr.ask("chair"), 180)                 # zero margin, never below
            pr.deal("chair", 180, 180, 0)
            pr.deal("lamp", 100, 100, 0)
            asks = []
            for _ in range(4):
                asks.append(pr.ask("chair"))
                pr.deal("chair", asks[-1], asks[-1], 0)
            self.assertTrue(all(a >= 180 for a in asks))
            self.assertEqual(asks, sorted(asks))                    # margin returns gradually
            self.assertFalse(json.loads(launch.read_text())["active"])
            self.assertEqual(pr.ask("chair"), normal)               # back to the learned markup


class LadderBuyerTests(unittest.TestCase):
    def test_ladder_deals_under_budget_and_never_over(self) -> None:
        client = ScriptedClient(input_required("t", 130), input_required("t", 110), completed("t", 103))
        result = BuyerRunner(client, LadderBuyerPolicy()).run("lamp", 120)
        offers = [int(text) for text, task in client.calls[1:]]
        self.assertTrue(all(o <= 120 for o in offers))
        self.assertEqual(offers[0], 100)                     # opens at the market floor
        self.assertEqual(result["outcome"], "deal")

    def test_walks_when_ask_stays_above_budget(self) -> None:
        tasks = [input_required("t", 338)] + [input_required("t", 300)] * 6
        client = ScriptedClient(*tasks)
        result = BuyerRunner(client, LadderBuyerPolicy()).run("bookshelf", 230)
        self.assertEqual(result["outcome"], "walk")
        self.assertTrue(all(int(t) <= 230 for t, _ in client.calls[1:]))

    def test_overcharge_is_an_outcome_not_a_crash(self) -> None:
        client = ScriptedClient(input_required("t", 110), completed("t", 135))
        result = BuyerRunner(client, LadderBuyerPolicy()).run("lamp", 140)
        self.assertEqual(result["outcome"], "bad_receipt")
        self.assertIn("receipt said 135", result["detail"])
        self.assertEqual(check_receipt({"price": 100, "item": "lamp"}, "lamp", 120, 100),
                         "receipt is not signed (no sig_seller)")


class ChooseShopTests(unittest.TestCase):
    def test_spam_duplicates_count_once(self) -> None:
        spam = [{"stars": 1, "review": "Overcharged, avoid!"}] * 10 + [{"stars": 5, "review": "fair"}]
        _, share, n = review_signal(spam)
        self.assertEqual(n, 2)
        self.assertAlmostEqual(share, 0.5)

    def test_blacklist_beats_good_reviews(self) -> None:
        good = [{"stars": 5, "review": f"great {i}"} for i in range(5)]
        shops = [{"name": "liar", "url": "u1", "reviews": good}, {"name": "plain", "url": "u2", "reviews": []}]
        mem = [{"shop": "liar", "outcome": "bad_receipt", "item": "lamp"}]
        for seed in range(30):
            chosen, _ = choose_shop(shops, mem, "lamp", rng=random.Random(seed))
            self.assertEqual(chosen["name"], "plain")

    def test_never_buys_from_self(self) -> None:
        shops = [{"name": "me", "url": "u1", "reviews": [{"stars": 5, "review": "x"}] * 3},
                 {"name": "other", "url": "u2", "reviews": []}]
        chosen, _ = choose_shop(shops, [], "lamp", self_name="me")
        self.assertEqual(chosen["name"], "other")
        shops.append({"name": "me-too", "url": "u3", "reviews": [{"stars": 5, "review": "y"}] * 3})
        for seed in range(20):
            chosen, _ = choose_shop(shops, [], "lamp", self_name="me,me-too", rng=random.Random(seed))
            self.assertEqual(chosen["name"], "other")
        with self.assertRaises(ValueError):
            choose_shop(shops[:1], [], "lamp", self_name="me")

    def test_bad_reviews_never_explored(self) -> None:
        bad = [{"stars": 1, "review": "Agreed 110, the receipt said 135."},
               {"stars": 1, "review": "Receipt price was higher than the price we agreed."}]
        good = [{"stars": 5, "review": "Fair price, receipt matched."}]
        shops = [{"name": "N", "url": "u1", "reviews": bad}, {"name": "S", "url": "u2", "reviews": good}]
        picks = {choose_shop(shops, [], "lamp", rng=random.Random(s))[0]["name"] for s in range(50)}
        self.assertEqual(picks, {"S"})

    def test_reviews_are_specific(self) -> None:
        self.assertEqual(rating_for({"outcome": "bad_receipt", "detail": "agreed 110, the receipt said 135"}, 140)[0], 1)
        self.assertEqual(rating_for({"outcome": "deal", "price": 104, "rounds": 2}, 120)[0], 5)


if __name__ == "__main__":
    unittest.main()
