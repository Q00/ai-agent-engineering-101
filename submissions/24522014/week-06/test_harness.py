"""Local adapter checks. Child processes here are Python fixtures, never models."""

import json
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import harness


class HarnessChecks(unittest.TestCase):
    def test_all_pairings(self):
        for mode, expected in (("claude", ("claude", "claude")),
                               ("codex", ("codex", "codex")),
                               ("mixed", ("claude", "codex"))):
            self.assertEqual(tuple(harness.select_harness(mode, role)
                                   for role in ("seller", "buyer")), expected)
        self.assertEqual(tuple(harness.select_harness("mixed", role, "codex", "claude")
                               for role in ("seller", "buyer")), ("codex", "claude"))

    def test_invalid_price_and_shape(self):
        invalid = [True, 1.5, "110", 0, -1, None]
        for price in invalid:
            with self.subTest(price=price), self.assertRaises(ValueError):
                harness.parse_response(json.dumps({"action": "ask", "price": price, "reason": "test"}))
        for text in ('[]', '{}', '```json\n{}\n```',
                     '{"action":"reject","price":110,"reason":"test"}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                harness.parse_response(text)

    def test_valid_actions(self):
        for action in ("ask", "accept", "offer", "reject", "walk_away"):
            value = {"action": action, "price": None if action in ("reject", "walk_away") else 110,
                     "reason": "test"}
            self.assertEqual(harness.parse_response(json.dumps(value)), value)

    def run_fixture(self, code, timeout=5):
        # Keep test-process evidence separate from real model invocations; never delete logs.
        root = harness.ROOT / "logs/step2-tests" / uuid4().hex
        with patch.object(harness, "ROOT", root), patch.object(
                harness, "build_command", return_value=[sys.executable, "-c", code]):
            try:
                result = harness.run_harness("claude", "UNIT TEST: local Python fixture, no model", timeout)
            except harness.HarnessError:
                result = None
        log_dir = next((root / "logs/harness").iterdir())
        return result, log_dir, json.loads((log_dir / "metadata.json").read_text(encoding="utf-8"))

    def test_nonzero_exit_keeps_raw_error(self):
        result, logs, metadata = self.run_fixture("import sys; print('fixture error', file=sys.stderr); sys.exit(7)")
        self.assertIsNone(result)
        self.assertEqual(metadata["status"], "failed")
        self.assertEqual(metadata["returncode"], 7)
        self.assertIn("fixture error", (logs / "stderr.txt").read_text())

    def test_timeout_keeps_partial_output(self):
        result, logs, metadata = self.run_fixture(
            "import time; print('fixture started', flush=True); time.sleep(30)", timeout=1)
        self.assertIsNone(result)
        self.assertEqual(metadata["status"], "failed")
        self.assertIn("exceeded", metadata["error"])
        self.assertIsNotNone(metadata["returncode"])
        self.assertIn("fixture started", (logs / "stdout.txt").read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
