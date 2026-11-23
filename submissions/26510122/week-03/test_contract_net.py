"""Offline checks for parsing, tie-breaking, and required message accounting."""

import json
import unittest

from contract_net import Bid, choose_winner, parse_bid, run_contract_net


class ContractNetTests(unittest.TestCase):
    def test_invalid_bid_becomes_refusal(self):
        bid = parse_bid("developer", "not json")
        self.assertFalse(bid.participate)
        self.assertEqual(bid.confidence, 0)
        self.assertIsNotNone(bid.parse_error)

    def test_first_bid_wins_a_tie(self):
        bids = [
            Bid("developer", True, 80, "fit", "{}"),
            Bid("writer", True, 80, "fit", "{}"),
        ]
        self.assertEqual(choose_winner(bids).contractor, "developer")

    def test_gold_is_hidden_and_messages_are_counted(self):
        tasks = [{"id": "t1", "desc": "debug Python", "gold": "developer"}]
        seen_users = []

        def fake_chat(system, user):
            seen_users.append(json.loads(user))
            confidence = 90 if "contractor developer" in system else 30
            return json.dumps({
                "participate": True,
                "confidence": confidence,
                "reason": "offline test",
            })

        metrics = run_contract_net(tasks, "baseline", fake_chat, lambda *a, **k: None)
        self.assertEqual(metrics["correct"], 1)
        self.assertEqual(metrics["messages"], 7)
        self.assertTrue(all("gold" not in announcement for announcement in seen_users))

    def test_refusals_are_not_counted_as_bids(self):
        tasks = [{"id": "t1", "desc": "unknown work", "gold": "developer"}]

        def refuse(system, user):
            return json.dumps({
                "participate": False,
                "confidence": 10,
                "reason": "outside my specialty",
            })

        metrics = run_contract_net(tasks, "baseline", refuse, lambda *a, **k: None)
        self.assertEqual(metrics["messages"], 3)
        self.assertEqual(metrics["unassigned"], 1)

    def test_parse_failures_are_counted(self):
        tasks = [{"id": "t1", "desc": "debug Python", "gold": "developer"}]
        replies = iter([
            "not json",
            json.dumps({
                "participate": False,
                "confidence": 10,
                "reason": "outside my specialty",
            }),
            json.dumps({
                "participate": True,
                "confidence": 30,
                "reason": "can attempt it",
            }),
        ])

        metrics = run_contract_net(
            tasks, "baseline", lambda system, user: next(replies),
            lambda *a, **k: None,
        )

        self.assertEqual(metrics["parse_fails"], 1)


if __name__ == "__main__":
    unittest.main()
