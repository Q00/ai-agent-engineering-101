import copy
import io
import json
import unittest

import smoke
from transport import ConfigurationError, OpenRouterClient


class PilotTests(unittest.TestCase):
    def run_episode(self, labels):
        calls = []
        labels = iter(labels)

        def complete(messages, emit, task_id, contractor, *, response_format):
            calls.append((contractor, copy.deepcopy(messages), copy.deepcopy(response_format)))
            if contractor == "reader":
                act, price = next(labels)
                return json.dumps({"performative": act, "price": price})
            return f"Public message {len(calls)}."

        result = {"deal_possible": 1, "price": "", "reader_calls": 0, "format_errors": 0}
        scenario = {"id": "test", "item": "bicycle", "reserve": 120, "budget": 150}
        smoke.negotiate(complete, scenario, lambda *a, **kw: None, result)
        return result, calls

    def test_full_context_and_private_limits(self):
        result, calls = self.run_episode([("propose", 130), ("accept-proposal", None)])
        self.assertEqual((result["outcome"], result["price"], result["correct"]), ("deal", 130, 1))
        buyer, seller = calls[0][1], calls[2][1]
        self.assertIn("150", buyer[0]["content"])
        self.assertNotIn("120", buyer[0]["content"])
        self.assertIn("120", seller[0]["content"])
        self.assertNotIn("150", seller[0]["content"])
        self.assertEqual(seller[1], {"role": "user", "content": "Public message 1."})
        for index, (_, messages, fmt) in enumerate(calls[1::2], 1):
            self.assertEqual(len(json.loads(messages[1]["content"])), index)
            self.assertNotIn("private", json.dumps(messages))
            self.assertEqual(fmt["json_schema"]["strict"], True)

    def test_eighth_message_can_make_a_deal(self):
        result, calls = self.run_episode([("propose", 130)] * 7 + [("accept-proposal", 140)])
        self.assertEqual((result["turns"], result["outcome"], result["price"]), (8, "deal", 130))
        self.assertEqual(len(calls), 16)
        # Third speaker invocation has own prior assistant and opponent user message.
        self.assertEqual([m["role"] for m in calls[4][1]], ["system", "user", "assistant", "user"])

    def test_eight_nonterminal_messages_are_open(self):
        result, _ = self.run_episode([("reject-proposal", None)] * 8)
        self.assertEqual((result["outcome"], result["turns"], result["correct"]), ("open", 8, 0))

    def test_invalid_acceptance_is_recorded_and_continues(self):
        result, _ = self.run_episode([("accept-proposal", None), ("refuse", None)])
        self.assertEqual((result["outcome"], result["format_errors"], result["turns"]), ("no_deal", 1, 2))

    def test_violation_is_measured_not_corrected(self):
        result, _ = self.run_episode([("propose", 90), ("accept-proposal", None)])
        self.assertEqual((result["price"], result["violation"], result["correct"]), (90, 1, 0))

    def test_local_schema_and_business_validation(self):
        for bad in [[], {"performative": "query", "price": None},
                    {"performative": "propose", "price": True},
                    {"performative": "propose", "price": None},
                    {"performative": "propose", "price": -1},
                    {"performative": "refuse", "price": None, "extra": 0}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                smoke.parse_label(json.dumps(bad))

    def test_serialized_wire_formats_and_fail_closed(self):
        payloads = []

        def opener(request, timeout):
            payloads.append(json.loads(request.data))
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": "ok"},
                                                      "finish_reason": "stop"}]}).encode())

        config = json.loads((smoke.HERE / "config.json").read_text())
        client = OpenRouterClient("test-placeholder", config, opener=opener)
        for role, fmt in (("buyer", smoke.TEXT_FORMAT), ("seller", smoke.TEXT_FORMAT),
                          ("reader", smoke.READER_FORMAT)):
            client.complete([], lambda *a, **kw: None, "test", role, response_format=fmt)
            self.assertEqual(payloads[-1]["response_format"], fmt)
            self.assertEqual(payloads[-1]["temperature"], 1.0)
            self.assertEqual(payloads[-1]["top_p"], 0.95)
            self.assertTrue(payloads[-1]["provider"]["require_parameters"])
        with self.assertRaises(ConfigurationError):
            client.complete([], lambda *a, **kw: None, "test", "buyer")
        self.assertEqual(len(payloads), 3)


if __name__ == "__main__":
    unittest.main()
