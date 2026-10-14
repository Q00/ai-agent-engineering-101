import json
import unittest

from competitive_negotiation import (
    Offer,
    SellerDecision,
    award_affinity,
    buyer_penalty,
    effective_offer,
    evaluate_competitive_outcome,
    parse_structured_offer,
    parse_structured_seller_decision,
    run_competitive_episode,
    seller_penalties,
)


class SequenceModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, system, messages):
        self.calls.append((system, [dict(message) for message in messages]))
        if not self.responses:
            raise AssertionError("model response queue is empty")
        return self.responses.pop(0)


def structured_offer(price):
    return json.dumps(
        {"performative": "propose", "content": {"price": price}},
        separators=(",", ":"),
    )


def structured_decision(performative, buyer_id=None):
    content = {} if buyer_id is None else {"buyer_id": buyer_id}
    return json.dumps(
        {"performative": performative, "content": content},
        separators=(",", ":"),
    )


class ScoringTests(unittest.TestCase):
    def test_affinity_goes_to_strictly_highest_valid_offer(self):
        affinity = {"buyer_1": 0, "buyer_2": 1}
        offers = {
            "buyer_1": Offer("buyer_1", 90),
            "buyer_2": Offer("buyer_2", 85),
        }
        updated, awarded = award_affinity(
            affinity,
            offers,
            {"buyer_1": 100, "buyer_2": 95},
        )
        self.assertEqual(updated, {"buyer_1": 1, "buyer_2": 1})
        self.assertEqual(awarded, "buyer_1")

    def test_invalid_high_offer_cannot_earn_affinity(self):
        offers = {
            "buyer_1": Offer("buyer_1", 110),
            "buyer_2": Offer("buyer_2", 90),
        }
        updated, awarded = award_affinity(
            {"buyer_1": 0, "buyer_2": 0},
            offers,
            {"buyer_1": 100, "buyer_2": 95},
        )
        self.assertEqual(updated, {"buyer_1": 0, "buyer_2": 1})
        self.assertEqual(awarded, "buyer_2")

    def test_tied_offers_do_not_change_affinity(self):
        offers = {
            "buyer_1": Offer("buyer_1", 90),
            "buyer_2": Offer("buyer_2", 90),
        }
        updated, awarded = award_affinity(
            {"buyer_1": 0, "buyer_2": 0},
            offers,
            {"buyer_1": 100, "buyer_2": 100},
        )
        self.assertEqual(updated, {"buyer_1": 0, "buyer_2": 0})
        self.assertIsNone(awarded)

    def test_penalties_are_normalized_by_private_limits(self):
        self.assertAlmostEqual(buyer_penalty(110, 100), 10.0)
        self.assertEqual(buyer_penalty(90, 100), 0.0)
        reserve_penalty, opportunity_penalty = seller_penalties(
            accepted_price=70,
            reserve=80,
            highest_valid_offer=90,
        )
        self.assertAlmostEqual(reserve_penalty, 12.5)
        self.assertAlmostEqual(opportunity_penalty, 100 * 20 / 90)

    def test_affinity_is_one_percent_of_reserve_per_point(self):
        self.assertEqual(effective_offer(91, affinity=2, reserve=100), 93)


class ProtocolParserTests(unittest.TestCase):
    def test_structured_offer_parser(self):
        self.assertEqual(
            parse_structured_offer(structured_offer(90), "buyer_1"),
            Offer("buyer_1", 90),
        )

    def test_structured_seller_parser(self):
        self.assertEqual(
            parse_structured_seller_decision(
                structured_decision("accept-proposal", "buyer_2")
            ),
            SellerDecision("accept-proposal", "buyer_2"),
        )


class OutcomeTests(unittest.TestCase):
    SCENARIO = {
        "reserve": 80,
        "budgets": {"buyer_1": 100, "buyer_2": 95},
    }

    def test_correct_deal_respects_seller_and_winner_limits(self):
        self.assertEqual(
            evaluate_competitive_outcome(
                self.SCENARIO, "deal", "buyer_1", 90
            ),
            (1, 0),
        )

    def test_deal_below_reserve_is_a_violation(self):
        self.assertEqual(
            evaluate_competitive_outcome(
                self.SCENARIO, "deal", "buyer_2", 70
            ),
            (0, 1),
        )

    def test_no_deal_is_correct_only_when_no_buyer_can_meet_reserve(self):
        impossible = {
            "reserve": 120,
            "budgets": {"buyer_1": 100, "buyer_2": 110},
        }
        self.assertEqual(
            evaluate_competitive_outcome(impossible, "no_deal", None, None),
            (1, 0),
        )


class EpisodeTests(unittest.TestCase):
    SCENARIO = {
        "id": "headphones",
        "item": "headphones",
        "reserve": 80,
        "budgets": {"buyer_1": 100, "buyer_2": 95},
    }

    def test_rejected_round_broadcasts_highest_and_awards_affinity(self):
        model = SequenceModel(
            [
                structured_offer(90),
                structured_offer(85),
                structured_decision("reject-proposal"),
                structured_offer(95),
                structured_offer(90),
                structured_decision("accept-proposal", "buyer_1"),
            ]
        )
        events = []
        result = run_competitive_episode(
            self.SCENARIO,
            "structured",
            model,
            events.append,
            max_rounds=2,
        )
        self.assertEqual(result["outcome"], "deal")
        self.assertEqual(result["winner"], "buyer_1")
        self.assertEqual(result["price"], 95)
        self.assertEqual(result["affinity_buyer_1"], 1)
        self.assertEqual(result["reader_calls"], 0)
        feedback = [event for event in events if event["event"] == "round_feedback"]
        self.assertEqual(feedback[0]["highest_valid_offer"], 90)
        self.assertEqual(feedback[0]["affinity_awarded"], "buyer_1")

    def test_overbudget_offer_is_penalized_and_not_rewarded(self):
        model = SequenceModel(
            [
                structured_offer(110),
                structured_offer(90),
                structured_decision("reject-proposal"),
                structured_offer(100),
                structured_offer(95),
                structured_decision("refuse"),
            ]
        )
        result = run_competitive_episode(
            self.SCENARIO,
            "structured",
            model,
            lambda _: None,
            max_rounds=2,
        )
        self.assertEqual(result["outcome"], "no_deal")
        self.assertAlmostEqual(result["penalty_buyer_1"], 10.0)
        self.assertEqual(result["affinity_buyer_1"], 0)
        self.assertEqual(result["affinity_buyer_2"], 1)


if __name__ == "__main__":
    unittest.main()
