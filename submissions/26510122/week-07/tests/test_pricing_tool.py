"""Validate the narrow model-facing data boundary."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness import decide
from pricing import PricingRecorder
from pricing_tool import dispatch, get_price_stats


class PricingToolTest(unittest.TestCase):
    def test_aggregate_read_only_and_unknowns_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pricing.sqlite"
            recorder = PricingRecorder(path)
            recorder.start("a", "lamp", 100)
            recorder.opening_sent("a", 112)
            recorder.start("b", "lamp", 100)
            recorder.opening_sent("b", 110)
            recorder.receipt("b", 105)
            before = path.read_bytes()
            result = get_price_stats(path, "lamp")
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(result["by_policy"][0]["attempts"], 2)
            self.assertEqual(result["by_policy"][0]["negotiation_unresolved"], 2)
            self.assertEqual(result["by_policy"][0]["receipts_pending_market"], 1)
            self.assertEqual(result["by_policy"][0]["market_counted"], 0)
            self.assertNotIn("task_id", json.dumps(result))

    def test_fixed_item_input_and_mcp_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pricing.sqlite"
            PricingRecorder(path)
            with self.assertRaises(ValueError):
                get_price_stats(path, "lamp' OR 1=1 --")
            listed = dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, path)
            self.assertEqual([tool["name"] for tool in listed["result"]["tools"]], ["get_price_stats"])
            self.assertTrue(listed["result"]["tools"][0]["annotations"]["readOnlyHint"])
            called = dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                               "params": {"name": "get_price_stats", "arguments": {"item": "lamp"}}}, path)
            self.assertEqual(called["result"]["structuredContent"]["item"], "lamp")
            refused = dispatch({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                "params": {"name": "get_price_stats", "arguments": {"item": "lamp", "sql": "DELETE FROM trades"}}}, path)
            self.assertTrue(refused["result"]["isError"])

    def test_model_tool_is_opt_in_and_opening_only(self):
        prompt = {"phase": "opening", "item": "lamp"}
        with patch.dict("harness.os.environ", {"WEEK07_PRICING_STATS_TOOL": "0"}):
            with patch("harness.decide_json", return_value={"action": "ask", "price": 112}) as mocked:
                decide("seller", prompt, ["ask"])
                self.assertIsNone(mocked.call_args.kwargs["pricing_stats_item"])
        with patch.dict("harness.os.environ", {"WEEK07_PRICING_STATS_TOOL": "1"}):
            with patch("harness.decide_json", return_value={"action": "ask", "price": 112}) as mocked:
                decide("seller", prompt, ["ask"])
                self.assertEqual(mocked.call_args.kwargs["pricing_stats_item"], "lamp")
                decide("seller", {**prompt, "phase": "follow_up"}, ["ask"])
                self.assertIsNone(mocked.call_args.kwargs["pricing_stats_item"])


if __name__ == "__main__":
    unittest.main()
