"""Unit tests for one negotiation episode."""

import unittest

from negotiate import run_episode


def queued_agent(messages: list[str]):
    queue = iter(messages)

    def call(_system, _history):
        return next(queue)

    return call


class EpisodeTests(unittest.TestCase):
    def test_structured_deal_uses_other_partys_last_price(self) -> None:
        result = run_episode(
            {"id": 1, "item": "a bicycle", "reserve": 90, "budget": 110},
            "structured",
            queued_agent([
                '{"performative":"propose","content":{"price":100}}',
                '{"performative":"accept-proposal","content":{"price":null}}',
            ]),
            None,
        )
        self.assertEqual(
            (result.outcome, result.price, result.correct, result.violation),
            ("deal", 100, 1, 0),
        )
        self.assertEqual((result.turns, result.reader_calls), (2, 0))

    def test_deal_outside_reserve_is_a_violation(self) -> None:
        result = run_episode(
            {"id": 2, "item": "a lamp", "reserve": 90, "budget": 110},
            "structured",
            queued_agent([
                '{"performative":"propose","content":{"price":80}}',
                '{"performative":"accept-proposal","content":{"price":null}}',
            ]),
            None,
        )
        self.assertEqual(
            (result.outcome, result.correct, result.violation),
            ("deal", 0, 1),
        )

    def test_refusal_is_correct_when_no_deal_is_possible(self) -> None:
        result = run_episode(
            {"id": 3, "item": "a camera", "reserve": 200, "budget": 160},
            "structured",
            queued_agent([
                '{"performative":"refuse","content":{"price":null}}',
            ]),
            None,
        )
        self.assertEqual(
            (result.outcome, result.correct, result.violation, result.turns),
            ("no_deal", 1, 0, 1),
        )

    def test_turn_limit_leaves_episode_open(self) -> None:
        messages = [
            '{"performative":"propose","content":{"price":50}}',
            '{"performative":"reject-proposal","content":{"price":null}}',
        ] * 4
        result = run_episode(
            {"id": 4, "item": "a book", "reserve": 40, "budget": 60},
            "structured",
            queued_agent(messages),
            None,
        )
        self.assertEqual((result.outcome, result.turns), ("open", 8))

    def test_unparseable_message_is_counted_and_conversation_continues(self) -> None:
        result = run_episode(
            {"id": 5, "item": "a book", "reserve": 70, "budget": 60},
            "structured",
            queued_agent([
                "not json",
                '{"performative":"refuse","content":{"price":null}}',
            ]),
            None,
        )
        self.assertEqual(
            (result.outcome, result.format_errors, result.turns, result.correct),
            ("no_deal", 1, 2, 1),
        )

    def test_accept_without_proposal_does_not_create_a_deal(self) -> None:
        result = run_episode(
            {"id": 6, "item": "a book", "reserve": 40, "budget": 60},
            "structured",
            queued_agent([
                '{"performative":"accept-proposal","content":{"price":null}}',
            ]),
            None,
            max_turns=1,
        )
        self.assertEqual((result.outcome, result.price), ("open", None))
        self.assertIn("without prior proposal", result.note)


if __name__ == "__main__":
    unittest.main()
