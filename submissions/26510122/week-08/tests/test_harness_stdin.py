import hashlib
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

import harness


class HarnessStdinTests(unittest.TestCase):
    def test_large_utf8_prompt_reaches_child_in_full(self):
        prompt = {"candidates": "매입" * 24000, "tail": "end of input"}
        expected = json.dumps(prompt, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertGreater(len(expected), 128 * 1024)
        schema = {
            "type": "object",
            "properties": {"sha256": {"type": "string"}},
            "required": ["sha256"],
            "additionalProperties": False,
        }
        fake_cli = """
import hashlib
import json
from pathlib import Path
import sys

assert sys.argv[-1] == "-"
answer = Path(sys.argv[sys.argv.index("--output-last-message") + 1])
answer.write_text(json.dumps({"sha256": hashlib.sha256(sys.stdin.buffer.read()).hexdigest()}))
"""
        run = subprocess.run

        def run_fake_cli(command, **kwargs):
            return run([sys.executable, "-c", fake_cli, *command[1:]], **kwargs)

        with patch("harness.subprocess.run", side_effect=run_fake_cli), patch("harness.trace"):
            answer = harness.decide_json("test buyer", prompt, schema)

        self.assertEqual(answer, {"sha256": hashlib.sha256(expected).hexdigest()})
