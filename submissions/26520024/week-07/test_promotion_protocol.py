"""Offline real-SDK promotion tests with an ephemeral in-memory signing key."""

import base64
import contextlib
import io
import json
import re
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.testclient import TestClient

from core import last_price
import rps_promotion
from seller import build, canon
import test_lab2
from wire import task_text


send = test_lab2.SignedProtocol.send
decision = test_lab2.SignedProtocol.decision


class PromotionProtocolTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.output = io.StringIO()
        self.stack.enter_context(contextlib.redirect_stdout(self.output))
        self.key = Ed25519PrivateKey.generate()
        self.decision = Mock(side_effect=decision)
        self.choose = self.stack.enter_context(
            patch.object(rps_promotion.secrets, "choice", return_value="rock")
        )
        self.client = self.stack.enter_context(
            TestClient(build(9996, decision=self.decision, signing_key=self.key))
        )

    def assert_pending(self, task, quote):
        self.assertEqual(task["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
        self.assertFalse(task.get("artifacts"))
        self.assertEqual(last_price(task_text(task)), quote)

    def proof(self, task):
        text = task_text(task)
        self.assertIn("Proof: ", text)
        proof, _ = json.JSONDecoder().raw_decode(text.split("Proof: ", 1)[1])
        return proof

    def assert_signed_completion(self, task_id, price):
        last = send(self.client, str(price), task_id)
        self.assertEqual(last["status"]["state"], "TASK_STATE_COMPLETED")
        receipt = last["artifacts"][0]["parts"][0]["data"]
        self.assertEqual(receipt["price"], price)
        self.assertEqual(receipt["task"], task_id)
        self.assertEqual(receipt["item"], "lamp")
        self.assertEqual(set(receipt), {"shop", "item", "price", "task", "sig_seller"})
        body = {field: receipt[field] for field in ("shop", "item", "price", "task")}
        body["price"] = int(body["price"])
        self.key.public_key().verify(base64.b64decode(receipt["sig_seller"]), canon(body))

    def test_win_loss_draw_quote_then_separate_signed_purchase(self):
        for move, outcome, quote in (("paper", "win", 100),
                                     ("scissors", "loss", 110),
                                     ("rock", "draw", 110)):
            with self.subTest(outcome=outcome):
                first = send(self.client, "I want a lamp")
                self.assert_pending(first, 110)
                opening = task_text(first)
                match = re.search(r"Commitment: ([0-9a-f]{64})", opening)
                self.assertIsNotNone(match)
                model_calls = self.decision.call_count
                choice_calls = self.choose.call_count
                played = send(self.client, "RPS " + move, first["id"])
                self.assert_pending(played, quote)
                self.assertEqual(played["id"], first["id"])
                self.assertEqual(self.decision.call_count, model_calls)
                self.assertEqual(self.choose.call_count, choice_calls)
                proof = self.proof(played)
                self.assertEqual(proof["task"], first["id"])
                self.assertEqual(proof["buyer_move"], move)
                self.assertEqual(proof["dealer_move"], "rock")
                self.assertEqual(proof["outcome"], outcome)
                self.assertEqual(proof["price"], quote)
                self.assertEqual(proof["commitment"], match.group(1))
                self.assertNotIn(proof["nonce"], opening)
                self.assertTrue(rps_promotion.verify_result(proof))
                self.assert_signed_completion(first["id"], quote)
                self.assertEqual(self.decision.call_count, model_calls + 1)

    def test_repeated_game_does_not_reroll_or_change_quote(self):
        first = send(self.client, "lamp")
        played = send(self.client, "RPS paper", first["id"])
        proof = self.proof(played)
        model_calls, choice_calls = self.decision.call_count, self.choose.call_count
        repeated = send(self.client, "RPS scissors", first["id"])
        self.assert_pending(repeated, 100)
        self.assertEqual(self.proof(repeated), proof)
        self.assertEqual(self.decision.call_count, model_calls)
        self.assertEqual(self.choose.call_count, choice_calls)
        self.assert_signed_completion(first["id"], 100)

    def test_malformed_game_is_not_numeric_purchase_and_can_retry_validly(self):
        first = send(self.client, "lamp")
        model_calls = self.decision.call_count
        for text in ("RPS rock 200", "Please play RPS rock; my budget is 120"):
            malformed = send(self.client, text, first["id"])
            self.assert_pending(malformed, 110)
            self.assertEqual(self.decision.call_count, model_calls)
            self.assertEqual(self.choose.call_count, 1)
            self.assertIn("syntax", task_text(malformed))
        valid = send(self.client, "rPs PAPER", first["id"])
        self.assert_pending(valid, 100)
        self.assertEqual(self.proof(valid)["outcome"], "win")
        self.assertEqual(self.decision.call_count, model_calls)
        self.assertEqual(self.choose.call_count, 1)
        self.assert_signed_completion(first["id"], 100)

    def test_loss_after_lower_counter_preserves_that_current_quote(self):
        def lower_counter(system, state, validator, *args):
            if state["item_id"] is None:
                action, price = "ask", 110
            elif state["incoming_offer"] == 90:
                action, price = "ask", 105
            else:
                action, price = "accept", state["incoming_offer"]
            return validator(dict(action=action, item_id="lamp", price=price,
                                  summary="scripted lower counter"))

        self.decision.side_effect = lower_counter
        first = send(self.client, "lamp")
        lowered = send(self.client, "90", first["id"])
        self.assert_pending(lowered, 105)
        model_calls = self.decision.call_count
        played = send(self.client, "RPS scissors", first["id"])
        self.assert_pending(played, 105)
        self.assertEqual(self.proof(played)["outcome"], "loss")
        self.assertEqual(self.proof(played)["price"], 105)
        self.assertEqual(self.decision.call_count, model_calls)
        self.assertEqual(self.choose.call_count, 1)
        self.assert_signed_completion(first["id"], 105)

    def test_no_promotion_offered_at_reserve_and_game_request_cannot_buy(self):
        def at_reserve(system, state, validator, *args):
            return validator(dict(
                action="ask" if state["item_id"] is None else "accept",
                item_id="lamp", price=100, summary="scripted reserve quote",
            ))

        self.decision.side_effect = at_reserve
        first = send(self.client, "lamp")
        self.assert_pending(first, 100)
        self.assertNotIn("Commitment:", task_text(first))
        self.choose.assert_not_called()
        model_calls = self.decision.call_count
        played = send(self.client, "RPS paper", first["id"])
        self.assert_pending(played, 100)
        self.assertIn("No discount game", task_text(played))
        self.assertEqual(self.decision.call_count, model_calls)
        self.choose.assert_not_called()
        self.assert_signed_completion(first["id"], 100)

    def test_buyer_can_ignore_game_and_negotiate_normally(self):
        first = send(self.client, "lamp")
        self.assert_pending(first, 110)
        self.assertIn("Optional", task_text(first))
        self.assert_signed_completion(first["id"], 100)
        self.assertEqual(self.decision.call_count, 2)
        self.assertEqual(self.choose.call_count, 1)
        self.assertNotIn("seller_promotion_played", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
