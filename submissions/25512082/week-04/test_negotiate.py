import json
import unittest

from negotiate import calculate_metrics, run_episode


POSSIBLE = {"id": "T1", "item": "item", "reserve": 40, "budget": 55}
IMPOSSIBLE = {"id": "T2", "item": "item", "reserve": 55, "budget": 40}


def structured(performative, price=None):
    return json.dumps({"performative": performative, "content": {"price": price}})


class ScriptedAgent:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def __call__(self, role, system, history):
        self.calls.append((role, system, history))
        return next(self.responses)


class NegotiationTests(unittest.TestCase):
    def test_buyer_starts_and_accept_uses_opponent_proposal(self):
        agent = ScriptedAgent(
            [
                structured("propose", 45),
                structured("propose", 50),
                structured("accept-proposal"),
            ]
        )
        result = run_episode(POSSIBLE, "structured", agent, None)
        self.assertEqual([call[0] for call in agent.calls], ["buyer", "seller", "buyer"])
        self.assertEqual(result.outcome, "deal")
        self.assertEqual(result.price, 50)  # seller's offer, not buyer's 45

    def test_seller_accept_uses_buyers_proposal(self):
        agent = ScriptedAgent(
            [structured("propose", 45), structured("accept-proposal")]
        )
        result = run_episode(POSSIBLE, "structured", agent, None)
        self.assertEqual(result.price, 45)

    def test_refuse_ends_no_deal(self):
        agent = ScriptedAgent([structured("refuse")])
        result = run_episode(IMPOSSIBLE, "structured", agent, None)
        self.assertEqual((result.outcome, result.turns, result.correct), ("no_deal", 1, 1))

    def test_eight_messages_end_open(self):
        agent = ScriptedAgent([structured("reject-proposal")] * 8)
        result = run_episode(POSSIBLE, "structured", agent, None)
        self.assertEqual((result.outcome, result.turns, result.correct), ("open", 8, 0))

    def test_parse_failure_is_delivered_to_other_history(self):
        agent = ScriptedAgent(["not json", structured("refuse")])
        result = run_episode(POSSIBLE, "structured", agent, None)
        seller_first_history = agent.calls[1][2]
        self.assertEqual(seller_first_history, [{"role": "user", "content": "not json"}])
        self.assertEqual(result.format_errors, 1)


class MetricTests(unittest.TestCase):
    def test_possible_valid_deal(self):
        self.assertEqual(calculate_metrics(40, 55, "deal", 50), (1, 0))

    def test_possible_below_reserve(self):
        self.assertEqual(calculate_metrics(40, 55, "deal", 35), (0, 1))

    def test_possible_above_budget(self):
        self.assertEqual(calculate_metrics(40, 55, "deal", 60), (0, 1))

    def test_impossible_no_deal(self):
        self.assertEqual(calculate_metrics(55, 40, "no_deal", None), (1, 0))

    def test_impossible_deal(self):
        self.assertEqual(calculate_metrics(55, 40, "deal", 45), (0, 1))

    def test_open_is_incorrect(self):
        self.assertEqual(calculate_metrics(40, 55, "open", None), (0, 0))


if __name__ == "__main__":
    unittest.main()
