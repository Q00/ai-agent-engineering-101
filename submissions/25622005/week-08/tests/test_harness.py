"""model/harness.py: a failing command's message reaches the error, from stderr or else stdout."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from model import harness  # noqa: E402


class HarnessErrorTest(unittest.TestCase):
    def test_stdout_message_when_stderr_is_empty(self):
        # Claude Code reports a missing login or an unavailable model on stdout and exits 1
        with self.assertRaisesRegex(RuntimeError, r"harness exit 1: Invalid API key"):
            harness.ask("hi", 10, "echo 'Invalid API key · Please run /login'; exit 1")

    def test_stderr_first(self):
        with self.assertRaisesRegex(RuntimeError, r"harness exit 2: boom$"):
            harness.ask("hi", 10, "echo out; echo boom >&2; exit 2")

    def test_answer(self):
        self.assertEqual(harness.ask("hi", 10, "cat"), "hi")


if __name__ == "__main__":
    unittest.main()
