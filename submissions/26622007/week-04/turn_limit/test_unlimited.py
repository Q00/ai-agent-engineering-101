import csv
import json
import math
import unittest

import run_unlimited as ext


class UnlimitedTests(unittest.TestCase):
    def test_existing_terminal_episodes_replay_identically(self):
        with (ext.ROOT / "results.csv").open() as f:
            rows = [r for r in csv.DictReader(f) if r["outcome"] in ("deal", "no_deal")]
        scenarios = json.loads((ext.ROOT / "scenarios.json").read_text())
        for expected in rows:
            with self.subTest(run=expected["run"], scenario=expected["scenario"]):
                events = [json.loads(s) for s in (ext.ROOT / "logs" / (expected["run"] + ".jsonl")).read_text().splitlines()]
                replies = iter(e for e in events if str(e["scenario"]) == expected["scenario"] and e["event"] in ("message", "reader_output"))
                def call(role, messages, fmt):
                    reply = next(replies)
                    self.assertEqual(role, reply.get("speaker", "reader"))
                    return reply["text"]
                sc = next(s for s in scenarios if str(s["id"]) == expected["scenario"])
                result = {"deal_possible": int(expected["deal_possible"])}
                ext.negotiate(sc, expected["condition"], call, lambda *a, **k: None, result, 180, clock=lambda: 0)
                for field in ("outcome", "price", "correct", "violation", "turns", "format_errors", "reader_calls"):
                    self.assertEqual(str(result[field]), expected[field])
                self.assertEqual(result["status"], "completed")

    def test_ninth_turn_can_close_with_full_history(self):
        calls = []
        def call(role, messages, fmt):
            calls.append((role, messages, fmt))
            return json.dumps({"performative": "accept-proposal" if len(calls) == 9 else "propose", "content": {"price": 35}})
        row = {"deal_possible": 1}
        ext.negotiate({"item": "lamp", "reserve": 30, "budget": 45}, "structured", call,
                      lambda *a, **k: None, row, 180, clock=lambda: 0)
        self.assertEqual((row["turns"], row["outcome"], row["price"]), (9, "deal", 35))
        self.assertEqual(len(calls[-1][1]), 9)
        self.assertTrue(all(c[2]["json_schema"]["strict"] for c in calls))

    def test_time_censoring_is_neither_refusal_nor_turn_limit_open(self):
        ticks = iter([0, 0, 181, 181])
        row = {"deal_possible": 0}
        ext.negotiate({"item": "keyboard", "reserve": 90, "budget": 70}, "structured",
                      lambda *a: '{"performative":"reject-proposal","content":{"price":null}}',
                      lambda *a, **k: None, row, 180, clock=lambda: next(ticks))
        self.assertEqual((row["turns"], row["status"], row["outcome"], row["correct"]), (1, "censored", "", ""))

    def test_request_budget_disabled_and_wire_format_preserved(self):
        payloads = []
        class Response:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read1(self, size):
                if getattr(self, "read", False): return b""
                self.read = True
                return json.dumps({"choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}]}).encode()
        def opener(request, **kwargs):
            payloads.append(json.loads(request.data))
            return Response()
        config = json.loads((ext.ROOT / "lab/config.json").read_text())
        config["max_http_requests"] = math.inf
        client = ext.lab.OpenRouterClient("fixture", config, opener=opener)
        client.request_count = 999
        for fmt in (ext.lab.TEXT_FORMAT, ext.lab.schema_format(), ext.lab.schema_format(True)):
            client.complete([], lambda *a, **k: None, 1, "fixture", response_format=fmt)
            self.assertEqual(payloads[-1]["response_format"], fmt)
            self.assertNotIn("max_http_requests", payloads[-1])
        self.assertEqual(client.request_count, 1002)


if __name__ == "__main__": unittest.main()
