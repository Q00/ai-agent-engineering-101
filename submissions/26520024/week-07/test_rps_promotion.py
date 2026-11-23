import copy
import hashlib
import json
import unittest
from unittest.mock import patch

import rps_promotion as rps


def scripted_promotion(move="rock", reserve=100):
    body = {"task": "task-1", "move": move, "nonce": "ab" * 16}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return {
        "task_id": body["task"], "reserve": reserve, "dealer_move": move,
        "nonce": body["nonce"], "commitment": hashlib.sha256(encoded).hexdigest(),
        "used": False,
    }


class PromotionTests(unittest.TestCase):
    def test_all_nine_move_pairs(self):
        expected = {
            ("rock", "rock"): "draw", ("rock", "paper"): "loss",
            ("rock", "scissors"): "win", ("paper", "rock"): "win",
            ("paper", "paper"): "draw", ("paper", "scissors"): "loss",
            ("scissors", "rock"): "loss", ("scissors", "paper"): "win",
            ("scissors", "scissors"): "draw",
        }
        for (buyer, dealer), outcome in expected.items():
            with self.subTest(buyer=buyer, dealer=dealer):
                promotion = scripted_promotion(dealer)
                original = copy.deepcopy(promotion)
                updated, result = rps.resolve(promotion, buyer, 115)
                self.assertEqual(result["outcome"], outcome)
                self.assertEqual(result["price"], 100 if outcome == "win" else 115)
                self.assertEqual(promotion, original)
                self.assertTrue(updated["used"])
                self.assertIsNot(updated, promotion)
                self.assertTrue(rps.verify_result(result))
                self.assertEqual(set(result), {
                    "task", "buyer_move", "dealer_move", "nonce", "commitment",
                    "outcome", "price",
                })

    def test_current_lower_quote_never_increases_on_loss_or_draw(self):
        for buyer in ("rock", "scissors"):
            for ask in (100, 101, 110):
                with self.subTest(buyer=buyer, ask=ask):
                    _, result = rps.resolve(scripted_promotion("rock"), buyer, ask)
                    self.assertEqual(result["price"], ask)

    def test_repeated_play_rejected(self):
        updated, _ = rps.resolve(scripted_promotion(), "paper", 120)
        with self.assertRaises(ValueError):
            rps.resolve(updated, "paper", 120)

    def test_new_promotion_uses_secret_randomness(self):
        with patch.object(rps.secrets, "choice", return_value="paper") as choose:
            with patch.object(rps.secrets, "token_hex", return_value="cd" * 16) as nonce:
                promotion = rps.new_promotion("task-2", 180)
        choose.assert_called_once_with(rps.MOVES)
        nonce.assert_called_once_with(16)
        self.assertFalse(promotion["used"])
        _, result = rps.resolve(promotion, "scissors", 190)
        self.assertTrue(rps.verify_result(result))

    def test_invalid_new_promotion_fields(self):
        for task, reserve in (("", 1), ("  ", 1), (None, 1), (1, 1),
                              ("t", True), ("t", 1.0), ("t", -1), ("t", "1")):
            with self.subTest(task=task, reserve=reserve), self.assertRaises(ValueError):
                rps.new_promotion(task, reserve)

    def test_invalid_buyer_moves(self):
        for move in (None, [], 1, True, "ROCK", "RPS rock", "lizard"):
            with self.subTest(move=move), self.assertRaises(ValueError):
                rps.resolve(scripted_promotion(), move, 120)

    def test_invalid_current_quotes(self):
        for ask in (99, -1, 120.0, "120", None, True):
            with self.subTest(ask=ask), self.assertRaises(ValueError):
                rps.resolve(scripted_promotion(), "rock", ask)

    def test_changed_dealer_commitment_rejected_before_resolution(self):
        for change in ({"nonce": "cd" * 16}, {"dealer_move": "paper"},
                       {"task_id": "other"}, {"commitment": "0" * 64},
                       {"commitment": "\u2603"}, {"reserve": True}, {"used": 0}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                rps.resolve(dict(scripted_promotion(), **change), "paper", 120)

    def test_invalid_promotion_shapes(self):
        for promotion in (None, [], {}, dict(scripted_promotion(), extra=True)):
            with self.subTest(promotion=promotion), self.assertRaises(ValueError):
                rps.resolve(promotion, "rock", 120)

    def test_reveal_tampering_invalidates_hash(self):
        _, result = rps.resolve(scripted_promotion(), "paper", 120)
        for change in ({"nonce": "cd" * 16}, {"dealer_move": "paper"},
                       {"task": "other"}, {"commitment": "0" * 64},
                       {"commitment": None}, {"nonce": 1}):
            with self.subTest(change=change):
                self.assertFalse(rps.verify_result(dict(result, **change)))
        for invalid in (None, [], {}, "receipt"):
            self.assertFalse(rps.verify_result(invalid))

    def test_verifier_explicitly_checks_hash_not_price_or_outcome(self):
        _, result = rps.resolve(scripted_promotion(), "paper", 120)
        self.assertTrue(rps.verify_result(dict(result, price=999, outcome="loss")))

    def test_parser_accepts_only_complete_opt_in_commands(self):
        for text, move in (("RPS rock", "rock"), (" rPs PAPER ", "paper"),
                           ("\tRPS\tscissors\n", "scissors")):
            self.assertEqual(rps.parse_move(text), move)
            self.assertTrue(rps.is_game_request(text))
        for text in ("RPS", "RPS lizard", "RPS rock 100", "RPS: rock"):
            self.assertIsNone(rps.parse_move(text))
            self.assertTrue(rps.is_game_request(text))
        for text in ("120", "I choose rock", "rock", "RPSrock", "", None, 1):
            self.assertIsNone(rps.parse_move(text))
            self.assertFalse(rps.is_game_request(text))

    def test_invitation_reveals_commitment_not_secret(self):
        promotion = scripted_promotion()
        text = rps.invitation(promotion)
        self.assertIn(promotion["commitment"], text)
        self.assertNotIn(promotion["nonce"], text)
        self.assertNotIn("dealer_move", text)
        self.assertIn("separate numeric agreement", text)
        self.assertIn("current asking price stays unchanged", text)
        self.assertFalse(promotion["used"])


if __name__ == "__main__":
    unittest.main()
