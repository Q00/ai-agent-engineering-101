"""STEP 7 decision-flow tests with synthetic harness answers and HTTP fixtures."""

import json
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

import buyer
from harness import HarnessError
from test_buyer_transport import task


def decision(action, price):
    return {"action": action, "price": price, "reason": "local STEP 7 fixture"}


class BuyerDecisionChecks(unittest.TestCase):
    def setUp(self):
        root = buyer.ROOT / "logs/step7-tests" / uuid4().hex
        (root / "prompts").mkdir(parents=True)
        (root / "prompts/buyer.txt").write_text(
            (buyer.ROOT / "prompts/buyer.txt").read_text(encoding="utf-8"), encoding="utf-8")
        root_patch = patch.object(buyer, "ROOT", root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.requests = []

    def client(self, replies):
        replies = iter(replies)

        def handler(request):
            if request.method == "GET":
                return httpx.Response(200, json={"supportedInterfaces": [{"protocolBinding": "JSONRPC",
                    "protocolVersion": "1.0", "url": "http://fixture/rpc"}]})
            body = json.loads(request.content)
            self.requests.append(body)
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": next(replies)})

        return buyer.BuyerTransport("http://fixture", "lamp", 120, transport=httpx.MockTransport(handler))

    def test_model_decides_offers_and_history_is_forwarded(self):
        counter = task()
        counter["status"]["message"]["parts"][0]["text"] = "lamp: asking 105"
        with self.client([task(), counter, task("COMPLETED", price=110)]) as client, patch.object(
                buyer, "run_harness", side_effect=[decision("offer", 80), decision("offer", 110)]) as model:
            result = buyer.run_autonomous(client, "codex")
            self.assertEqual(result["outcome"], "purchased")
            self.assertEqual(result["receipt"]["price"], 110)
            self.assertEqual([r["params"]["message"]["parts"][0]["text"] for r in self.requests[1:]], ["80", "110"])
            self.assertEqual(model.call_args.args[0], "codex")
            context = json.loads(model.call_args.args[1].split("Input data:\n", 1)[1])
            self.assertEqual(context["history"][1]["decision"]["price"], 80)
            self.assertEqual(context["current_ask"], 105)
            self.assertNotIn("reserve", context)

    def test_walk_away_cancels_without_offering(self):
        with self.client([task(), task("CANCELED")]) as client, patch.object(
                buyer, "run_harness", return_value=decision("walk_away", None)):
            result = buyer.run_autonomous(client, "claude")
            self.assertEqual(result["outcome"], "walked_away")
            self.assertEqual([r["method"] for r in self.requests], ["SendMessage", "CancelTask"])

    def test_over_budget_decision_is_corrected_not_clamped(self):
        with self.client([task(), task("COMPLETED", price=117)]) as client, patch.object(
                buyer, "run_harness", side_effect=[decision("offer", 125), decision("offer", 117)]) as model:
            result = buyer.run_autonomous(client, "claude")
            self.assertEqual(result["receipt"]["price"], 117)
            self.assertEqual(len(self.requests), 2)
            self.assertEqual(self.requests[1]["params"]["message"]["parts"][0]["text"], "117")
            self.assertIn("budget 120", model.call_args.args[1])
            self.assertEqual(model.call_count, 2)

    def test_two_invalid_decisions_fail_without_offer_or_success(self):
        with self.client([task()]) as client, patch.object(
                buyer, "run_harness", return_value=decision("offer", 121)) as model:
            with self.assertRaises(buyer.BuyerError):
                buyer.run_autonomous(client, "claude")
            self.assertEqual(model.call_count, 2)
            self.assertEqual(len(self.requests), 1)
            self.assertFalse((client.log_dir / "outcome.json").exists())
            record = json.loads((client.log_dir / "decision-01.json").read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "failed")

    def test_process_failure_does_not_switch_harness_or_retry(self):
        with self.client([task()]) as client, patch.object(
                buyer, "run_harness", side_effect=HarnessError("fixture process failure")) as model:
            with self.assertRaises(buyer.BuyerError):
                buyer.run_autonomous(client, "codex")
            self.assertEqual(model.call_count, 1)
            self.assertEqual(model.call_args.args[0], "codex")
            self.assertEqual(len(self.requests), 1)

    def test_turn_limit_is_an_error_not_a_model_withdrawal(self):
        with self.client([task(), task()]) as client, patch.object(
                buyer, "run_harness", return_value=decision("offer", 80)) as model:
            with self.assertRaisesRegex(buyer.BuyerError, "turn limit"):
                buyer.run_autonomous(client, "claude", max_turns=1)
            self.assertEqual(model.call_count, 1)
            self.assertEqual(len(self.requests), 2)

    def test_remaining_time_prevents_starting_another_model_call(self):
        with self.client([task()]) as client, patch.object(buyer, "run_harness") as model:
            client.deadline = time.monotonic() + 4
            with self.assertRaisesRegex(buyer.BuyerError, "Insufficient time"):
                buyer.run_autonomous(client, "claude")
            model.assert_not_called()

    def test_wrong_role_action_is_rejected(self):
        with self.assertRaises(ValueError):
            buyer.validate_buyer_decision(decision("ask", 110), 120)
        buyer.validate_buyer_decision(decision("walk_away", None), 120)


if __name__ == "__main__":
    unittest.main(verbosity=2)
