import json
import unittest

import run_luna as luna  # puts lab/ on sys.path
import experiment as english


class LunaTests(unittest.TestCase):
    def test_turn_limit_is_isolated_from_the_english_module(self):
        self.assertIsNot(luna.lab, english)
        self.assertEqual((english.MAX_TURNS, luna.lab.MAX_TURNS), (8, 30))
        self.assertEqual(luna.lab.FORMAT, english.FORMAT)
        self.assertEqual(luna.lab.READER_SYSTEM, english.READER_SYSTEM)

    def test_thirtieth_message_ends_open_without_a_thirty_first_call(self):
        calls = []
        def call(role, messages, fmt):
            calls.append(role)
            return '{"performative":"reject-proposal","content":{"price":null}}'
        row = {"deal_possible": 1}
        luna.lab.negotiate({"item": "lamp", "reserve": 30, "budget": 45}, "structured", call,
                           lambda *a, **k: None, row)
        self.assertEqual((row["turns"], row["outcome"], len(calls)), (30, "open", 30))
        self.assertEqual(luna.status_of(row), "turn_limit")
        self.assertEqual(calls[:2], ["buyer", "seller"])

    def test_wire_payload_carries_effort_formats_and_unsettable_sampling(self):
        payloads = []
        class Response:
            done = False
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read1(self, size):
                if self.done: return b""
                self.done = True
                return json.dumps({"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]}).encode()
        def opener(request, **kwargs):
            payloads.append(json.loads(request.data))
            return Response()
        configs, _ = luna.load_inputs()
        for effort in luna.EFFORTS:
            client = luna.lab.OpenRouterClient("fixture", configs[effort], opener=opener)
            for fmt in (luna.lab.TEXT_FORMAT, luna.lab.schema_format(), luna.lab.schema_format(True)):
                client.complete([], lambda *a, **k: None, 2, "fixture", response_format=fmt)
                sent = payloads[-1]
                self.assertEqual(sent["response_format"], fmt)
                self.assertEqual(sent["reasoning"], {"effort": effort})
                self.assertEqual(sent["model"], "openai/gpt-6-luna")
                self.assertIsNone(sent["temperature"])
                self.assertIsNone(sent["top_p"])
                self.assertEqual(sent["provider"]["only"], ["openai"])
                self.assertTrue(sent["provider"]["require_parameters"])
                self.assertNotIn("max_tokens", sent)

    def test_unknown_effort_and_prefilled_reasoning_are_rejected(self):
        base = json.loads((luna.HERE / "config.json").read_text())
        with self.assertRaises(ValueError):
            luna.effort_config(base, "medium")
        with self.assertRaises(ValueError):
            luna.effort_config({**base, "reasoning": {"effort": "low"}}, "max")

    def test_usage_meter_sums_and_leaves_missing_fields_blank(self):
        meter = luna.UsageMeter()
        meter.observe("request", {})
        meter.observe("usage", {"usage": {"prompt_tokens": 10, "completion_tokens": 40, "cost": 0.00002,
                                          "completion_tokens_details": {"reasoning_tokens": 30}}})
        meter.observe("request", {})
        meter.observe("usage", {"usage": {"prompt_tokens": 5, "completion_tokens": 7, "cost": 0.00001}})
        self.assertEqual(meter.row(), {"http_requests": 2, "prompt_tokens": 15, "completion_tokens": 47,
                                       "reasoning_tokens": "", "cost_usd": "0.0000300000"})


if __name__ == "__main__":
    unittest.main()
