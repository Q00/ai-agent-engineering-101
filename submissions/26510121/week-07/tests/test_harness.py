"""Offline checks for actual failure paths; responses here are simulated."""
import json
import unittest

import httpx

from negotiation.config import Settings
from negotiation.contracts import SellerContext, SellerDecision
from negotiation.harness import HarnessError, decide
from negotiation.state import SellerMemory


class MemoryLog:
    def __init__(self):
        self.events = []

    def record(self, event, **fields):
        self.events.append({"event": event, **fields})


def response(price, status="completed"):
    raw = json.dumps({"action": "counter", "item_id": "lamp", "price": price,
                      "reason": "A counteroffer.", "summary": "Countered the observed offer."})
    return {"id": "simulated-response", "model": "simulated-model", "status": status,
            "output": [{"type": "message", "content": [{"type": "output_text", "text": raw}]}]}


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings("fake-credential-for-offline-test")
        self.context = SellerContext("lamp", 100, "negotiating", 80)
        self.log = MemoryLog()

    def run_mock(self, handler):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return decide("seller", self.context, {"buyer_text": "80"}, self.settings, self.log, client)

    def test_valid_model_price_is_returned_and_strict_schema_is_requested(self):
        def handler(request):
            body = json.loads(request.content)
            self.assertTrue(body["text"]["format"]["strict"])
            self.assertFalse(body["store"])
            return httpx.Response(200, json=response(119))
        self.assertEqual(self.run_mock(handler).price, 119)

    def test_invalid_price_is_repaired_by_model_with_unchanged_reserve(self):
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=response(99 if len(requests) == 1 else 117))
        self.assertEqual(self.run_mock(handler).price, 117)
        second_input = json.loads(requests[1]["input"])
        self.assertEqual(second_input["trusted_context"]["reserve"], 100)
        self.assertEqual(second_input["repair"]["validation_error"], "price_below_reserve")
        self.assertEqual(len(requests), 2)

    def test_second_invalid_decision_fails_without_a_fallback_price(self):
        with self.assertRaisesRegex(HarnessError, "decision_invalid_after_repair"):
            self.run_mock(lambda request: httpx.Response(200, json=response(99)))
        self.assertEqual(sum(e["event"] == "model_request" for e in self.log.events), 2)
        self.assertFalse(any(e["event"] == "decision_validated" for e in self.log.events))

    def test_api_error_body_with_credentials_is_never_logged(self):
        with self.assertRaisesRegex(HarnessError, "api_http_401"):
            self.run_mock(lambda request: httpx.Response(401, json={"error": {
                "message": "Invalid key: " + self.settings.api_key}}))
        self.assertNotIn(self.settings.api_key, json.dumps(self.log.events))

    def test_network_error_is_safe_and_not_retried_as_a_decision(self):
        def handler(request):
            raise httpx.ConnectError("simulated sensitive network detail", request=request)
        with self.assertRaisesRegex(HarnessError, "api_network_error"):
            self.run_mock(handler)
        self.assertEqual(sum(e["event"] == "model_request" for e in self.log.events), 1)
        self.assertNotIn("sensitive network detail", json.dumps(self.log.events))

    def test_truncated_output_is_not_accepted_even_if_json_looks_valid(self):
        with self.assertRaisesRegex(HarnessError, "model_did_not_complete"):
            self.run_mock(lambda request: httpx.Response(200, json=response(119, "incomplete")))

    def test_task_memory_is_bounded_and_separate(self):
        first, second = SellerMemory("lamp", 100), SellerMemory("chair", 180)
        decision = SellerDecision(action="counter", item_id="lamp", price=119,
                                  reason="Counteroffer.", summary="Current state.")
        for i in range(7):
            first.apply(str(i), decision)
        self.assertEqual(len(first.recent), 4)
        self.assertEqual(first.rounds, 7)
        self.assertEqual(list(second.recent), [])
        self.assertIsNone(second.ask)
        self.assertEqual(first.observation("new offer")["verified_state"]["previous_ask"], 119)

    def test_settings_repr_does_not_reveal_key(self):
        self.assertNotIn(self.settings.api_key, repr(self.settings))


if __name__ == "__main__":
    unittest.main(verbosity=2)
