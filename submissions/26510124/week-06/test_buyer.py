"""Offline protocol/safety tests; the harness and HTTP server are both replaced."""
from contextlib import redirect_stdout
import io
import json
import time
import unittest
from unittest.mock import patch

import httpx

import buyer_agent


def task(state, text="", price=None):
    result = {"id": "negotiation-1", "contextId": "conversation-1",
              "status": {"state": "TASK_STATE_" + state,
                         "message": {"parts": [{"text": text}]}}}
    if price is not None:
        result["artifacts"] = [{"name": "receipt", "parts": [{"data": {"price": price}}]}]
    return result


class BuyerTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.script = []
        self.card = {"supportedInterfaces": [{"protocolBinding": "JSONRPC",
                     "protocolVersion": "1.0", "url": "http://shop.test/rpc"}]}
        self.client = httpx.Client(transport=httpx.MockTransport(self.handle))
        self.addCleanup(self.client.close)
        self.buyer = buyer_agent.Buyer(self.client, url="http://shop.test",
                                      item="lamp", budget=120)
        self.output = io.StringIO()
        output_context = redirect_stdout(self.output)
        output_context.__enter__()
        self.addCleanup(output_context.__exit__, None, None, None)

    def handle(self, request):
        self.requests.append(request)
        self.assertEqual(request.headers.get("A2A-Version"), "1.0")
        if request.method == "GET":
            self.assertEqual(request.url.path, "/.well-known/agent-card.json")
            return httpx.Response(200, json=self.card)
        self.assertEqual(request.url.path, "/rpc")
        body = json.loads(request.content)
        method, result = self.script.pop(0)
        self.assertEqual(body["method"], method)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    def post_bodies(self):
        return [json.loads(request.content) for request in self.requests if request.method == "POST"]

    def test_negotiates_one_task_with_version_header_and_authorized_receipt(self):
        self.script = [
            ("SendMessage", {"task": task("INPUT_REQUIRED", "lamp: asking 150")}),
            ("SendMessage", task("INPUT_REQUIRED", "95 is too low. Counteroffer: 110.")),
            ("SendMessage", task("COMPLETED", "Deal at 110", 110)),
        ]
        decisions = [{"action": "offer", "price": 95, "reason": "Opening bid"},
                     {"action": "accept", "price": 110, "reason": "Affordable ask"}]
        with patch.object(buyer_agent, "buyer_decision", side_effect=decisions) as decide:
            self.assertEqual(self.buyer.run(), "purchased")
        bodies = self.post_bodies()
        self.assertNotIn("taskId", bodies[0]["params"]["message"])
        for body, price in zip(bodies[1:], ["95", "110"]):
            self.assertEqual(body["params"]["message"]["taskId"], "negotiation-1")
            self.assertEqual(body["params"]["message"]["parts"], [{"text": price}])
        history = decide.call_args_list[-1].kwargs["history"]
        self.assertIn({"role": "buyer", "text": "95"}, history)
        self.assertIn('"price": 110', self.output.getvalue())
        self.assertIn("HTTP RESPONSE", self.output.getvalue())
        self.assertFalse(self.script)

    def test_over_budget_model_offer_is_never_sent(self):
        self.script = [("SendMessage", task("INPUT_REQUIRED", "asking 150")),
                       ("CancelTask", task("CANCELED"))]
        with patch.object(buyer_agent, "buyer_decision", return_value={
                "action": "offer", "price": 121, "reason": "Invalid bid"}):
            with self.assertRaisesRegex(buyer_agent.BuyerError, "within the budget"):
                self.buyer.run()
        bodies = self.post_bodies()
        self.assertEqual([body["method"] for body in bodies], ["SendMessage", "CancelTask"])
        self.assertEqual(bodies[-1]["params"], {"id": "negotiation-1"})

    def test_accept_must_match_current_ask_not_the_previous_offer(self):
        self.script = [("SendMessage", task("INPUT_REQUIRED", "90 is too low. Asking 110")),
                       ("CancelTask", task("CANCELED"))]
        with patch.object(buyer_agent, "buyer_decision", return_value={
                "action": "accept", "price": 90, "reason": "Wrong number"}):
            with self.assertRaisesRegex(buyer_agent.BuyerError, "does not match"):
                self.buyer.run()
        self.assertEqual(len(self.post_bodies()), 2)

    def test_model_walk_away_cancels_without_an_offer(self):
        self.script = [("SendMessage", task("INPUT_REQUIRED", "Firm price 300")),
                       ("CancelTask", task("CANCELED"))]
        with patch.object(buyer_agent, "buyer_decision", return_value={
                "action": "walk_away", "reason": "Firm ask is over budget"}):
            self.assertEqual(self.buyer.run(), "no_deal")
        self.assertEqual(self.post_bodies()[-1]["method"], "CancelTask")

    def test_deadline_expires_without_sending_the_models_price(self):
        self.script = [("SendMessage", task("INPUT_REQUIRED", "asking 110")),
                       ("CancelTask", task("CANCELED"))]

        def delayed_reply(**kwargs):
            self.assertGreater(kwargs["timeout"], 0)
            self.assertLessEqual(kwargs["timeout"], 90)
            self.buyer.deadline = time.monotonic() - 1
            return {"action": "accept", "price": 110, "reason": "Affordable"}

        with patch.object(buyer_agent, "buyer_decision", side_effect=delayed_reply):
            with self.assertRaisesRegex(buyer_agent.BuyerError, "deadline exceeded"):
                self.buyer.run()
        self.assertEqual(self.post_bodies()[-1]["method"], "CancelTask")
        self.assertFalse(self.script)

    def test_harness_failure_cancels_and_is_not_replaced_with_a_rule(self):
        self.script = [("SendMessage", task("INPUT_REQUIRED", "asking 110")),
                       ("CancelTask", task("CANCELED"))]
        with patch.object(buyer_agent, "buyer_decision", side_effect=buyer_agent.HarnessError("bad JSON")):
            with self.assertRaisesRegex(buyer_agent.HarnessError, "bad JSON"):
                self.buyer.run()
        self.assertEqual(len(self.post_bodies()), 2)

    def test_receipt_cannot_charge_a_different_even_affordable_price(self):
        self.script = [("SendMessage", task("INPUT_REQUIRED", "asking 110")),
                       ("SendMessage", task("COMPLETED", "Deal at 119", 119))]
        with patch.object(buyer_agent, "buyer_decision", return_value={
                "action": "accept", "price": 110, "reason": "Affordable"}):
            with self.assertRaisesRegex(buyer_agent.BuyerError, "authorized offer"):
                self.buyer.run()
        self.assertNotIn("PURCHASE", self.output.getvalue())
        self.assertFalse(self.script)

    def test_incompatible_agent_card_fails_before_starting_a_task(self):
        self.card["supportedInterfaces"][0]["protocolVersion"] = "0.3"
        with patch.object(buyer_agent, "buyer_decision") as decide:
            with self.assertRaisesRegex(buyer_agent.BuyerError, "no A2A 1.0"):
                self.buyer.run()
            decide.assert_not_called()
        self.assertEqual(len(self.requests), 1)


if __name__ == "__main__":
    unittest.main()
