import csv
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from model_client import redact_secrets
from run_experiment import HEADER, load_completed


class ResumeTests(unittest.TestCase):
    def write_results(self, rows):
        temporary = tempfile.TemporaryDirectory()
        path = Path(temporary.name) / "results.csv"
        with path.open("w", encoding="utf-8", newline="") as result_file:
            writer = csv.writer(result_file)
            writer.writerow(HEADER)
            writer.writerows(rows)
        return temporary, path

    def test_existing_key_skips_only_exact_episode(self):
        row = [1, "free", "S1", 1, "deal", 50, 1, 0, 2, 0, 2, ""]
        temporary, path = self.write_results([row])
        self.addCleanup(temporary.cleanup)
        completed = load_completed(path)
        self.assertIn(("free", 1, "S1"), completed)
        self.assertNotIn(("free", 2, "S1"), completed)
        self.assertNotIn(("tagged", 1, "S1"), completed)

    def test_crash_row_is_completed_and_not_repeated(self):
        crash = [1, "free", "S1", "", "", "", "", "", "", "", "", "crash: boom"]
        temporary, path = self.write_results([crash])
        self.addCleanup(temporary.cleanup)
        self.assertIn(("free", 1, "S1"), load_completed(path))


class SecretRedactionTests(unittest.TestCase):
    def test_configured_key_is_removed_from_exception_text(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key-value"}):
            clean = redact_secrets("request failed for test-key-value")
        self.assertEqual(clean, "request failed for [REDACTED]")


if __name__ == "__main__":
    unittest.main()
