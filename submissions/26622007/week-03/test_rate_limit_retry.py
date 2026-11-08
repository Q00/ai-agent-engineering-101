"""Bounded 429 recovery without schema loss, premature retries or wider error retries."""
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from contract_net import bid_response_format
from openrouter_client import CallError, ConfigurationError, ENDPOINT, OpenRouterClient, retry_after_seconds

ROOT = Path(__file__).resolve().parent


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "extensions/peer_dag/config.json").read_text())["transport"]
        self.events, self.delays, self.wire = [], [], []

    def emit(self, event, **fields):
        self.events.append((event, fields))

    def error(self, header=None, status=429):
        return HTTPError(ENDPOINT, status, "busy", {"Retry-After": header} if header else {}, io.BytesIO(b"busy"))

    def response(self):
        content = json.dumps({"bid": True, "confidence": 90, "reason": "fixture"})
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    def run_client(self, opener):
        client = OpenRouterClient("not-a-live-key", self.config, opener, self.delays.append)
        result = client.complete([], self.emit, "task", "B", response_format=bid_response_format())
        return client, result

    def test_five_429s_recover_with_exact_wire_and_capped_backoff(self):
        def opener(request, timeout):
            self.wire.append(request.data)
            if len(self.wire) <= 5:
                raise self.error()
            return self.response()
        client, result = self.run_client(opener)
        self.assertTrue(json.loads(result)["bid"])
        self.assertEqual(client.request_count, 6)
        self.assertEqual(self.delays, [15, 30, 60, 60, 60])
        self.assertEqual(len(set(self.wire)), 1)
        payload = json.loads(self.wire[0])
        self.assertEqual(payload["response_format"], bid_response_format())
        self.assertEqual(payload["provider"]["only"], ["fireworks"])
        self.assertEqual([value["payload"] for event, value in self.events if event == "request"], [payload]*6)

    def test_retry_after_seconds_and_date_are_server_minimums(self):
        self.assertEqual(retry_after_seconds("45"), 45)
        with patch("openrouter_client.time.time", return_value=0):
            self.assertEqual(retry_after_seconds("Thu, 01 Jan 1970 00:02:00 GMT"), 120)
        for invalid in (None, "", "NaN", "-1", "1.5", "not a date"):
            self.assertIsNone(retry_after_seconds(invalid))
        def opener(request, timeout):
            self.wire.append(request.data)
            if len(self.wire) == 1:
                raise self.error("120")
            return self.response()
        self.run_client(opener)
        self.assertEqual(self.delays, [120])

    def test_server_wait_over_budget_stops_without_retrying_early(self):
        def opener(*_, **__):
            raise self.error("301")
        with self.assertRaisesRegex(CallError, "wait budget"):
            self.run_client(opener)
        self.assertFalse(self.delays)
        self.assertEqual(sum(event == "request" for event, _ in self.events), 1)
        self.assertIn("retry_exhausted", [event for event, _ in self.events])

    def test_persistent_429_stops_after_six_requests(self):
        def opener(*_, **__):
            raise self.error()
        with self.assertRaisesRegex(CallError, "HTTP 429"):
            self.run_client(opener)
        self.assertEqual(sum(event == "request" for event, _ in self.events), 6)
        self.assertEqual(self.delays, [15, 30, 60, 60, 60])

    def test_cumulative_server_wait_budget_is_bounded(self):
        def opener(*_, **__):
            raise self.error("120")
        with self.assertRaisesRegex(CallError, "wait budget"):
            self.run_client(opener)
        self.assertEqual(self.delays, [120, 120])
        self.assertEqual(sum(event == "request" for event, _ in self.events), 3)

    def test_request_budget_blocks_retry_before_sleep(self):
        self.config["max_http_requests"] = 1
        def opener(*_, **__):
            raise self.error()
        with self.assertRaisesRegex(CallError, "request budget"):
            self.run_client(opener)
        self.assertFalse(self.delays)

    def test_other_errors_keep_existing_attempt_limit(self):
        for status, expected in ((500, 2), (401, 1), (400, 1)):
            with self.subTest(status=status):
                self.events.clear(); self.delays.clear()
                def opener(*_, **__):
                    raise self.error(status=status)
                with self.assertRaises(CallError):
                    self.run_client(opener)
                self.assertEqual(sum(event == "request" for event, _ in self.events), expected)
                self.assertEqual(self.delays, [2] if expected == 2 else [])
        self.events.clear(); self.delays.clear()
        def timeout(*_, **__):
            raise TimeoutError()
        with self.assertRaises(CallError):
            self.run_client(timeout)
        self.assertEqual(self.delays, [2])

    def test_invalid_retry_budget_stops_before_network(self):
        for field, value in (("max_attempts", 100), ("max_attempts", True),
                             ("initial_delay_seconds", -1), ("max_wait_seconds", float("inf"))):
            with self.subTest(field=field, value=value):
                original = self.config["rate_limit_retry"][field]
                self.config["rate_limit_retry"][field] = value
                with self.assertRaises(ConfigurationError):
                    self.run_client(lambda *_, **__: self.fail("network must not run"))
                self.config["rate_limit_retry"][field] = original


if __name__ == "__main__":
    unittest.main()
