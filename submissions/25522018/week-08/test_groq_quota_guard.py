"""Offline tests for Groq 429 shared cooldown guard (no API traffic)."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx

import groq_quota_guard as guard
import llm_harness


class QuotaGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "guard.json"
        p = patch.object(guard, "STATE_FILE", self.state)
        p.start()
        self.addCleanup(p.stop)

    @staticmethod
    def fail_tpd(*args, **kwargs):
        request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
        response = httpx.Response(429, request=request, headers={"retry-after": "120"},
                                 json={"error": {"message": "Rate limit on tokens per day (TPD): Limit 2000"}})
        try:
            raise httpx.HTTPStatusError("Groq HTTP 429", request=request, response=response)
        except httpx.HTTPStatusError as e:
            raise RuntimeError("Groq decision failed after 1 tries: Groq HTTP 429") from e

    def test_regular_decision_uses_model_once(self):
        with patch.object(llm_harness, "decide", return_value={"action":"offer", "price":50}) as live:
            self.assertEqual(guard.decide("s", {}, ["offer"]), {"action":"offer", "price":50})
            self.assertEqual(live.call_count, 1)
            self.assertEqual(live.call_args.kwargs["retries_override"], 1)

    def test_quota_response_triggers_cross_process_cooldown(self):
        with patch.object(llm_harness, "decide", side_effect=self.fail_tpd) as live:
            with self.assertRaisesRegex(RuntimeError, "Groq HTTP 429"):
                guard.decide("s", {}, ["offer"])
            data = json.loads(self.state.read_text())
            self.assertEqual(data["reason"], "TPD")
            self.assertEqual(data["model"], guard.MODEL)
            self.assertGreater(data["until"], __import__("time").time() + 115)
            with self.assertRaisesRegex(RuntimeError, "shared quota cooldown"):
                guard.decide("s", {}, ["offer"])
            self.assertEqual(live.call_count, 1)

    def test_non_rate_error_is_not_hidden(self):
        with patch.object(llm_harness, "decide", side_effect=RuntimeError("invalid credentials")):
            with self.assertRaisesRegex(RuntimeError, "invalid credentials"):
                guard.decide("s", {}, ["offer"])
        self.assertFalse(self.state.exists())

    def test_expired_state_does_not_block(self):
        self.state.write_text(json.dumps({"model":guard.MODEL,"until":1,"reason":"TPD"}))
        with patch.object(llm_harness, "decide", return_value={"action":"accept","price":100}):
            self.assertEqual(guard.decide("s", {}, ["accept"])["action"], "accept")

if __name__ == "__main__":
    unittest.main(verbosity=2)
