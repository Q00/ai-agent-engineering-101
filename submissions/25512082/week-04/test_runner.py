import csv
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from model_client import redact_secrets
from model_client import RateLimitError
from run_experiment import HEADER, load_completed, run_all


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

    def test_exhausted_429_preserves_existing_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            (base / "scenarios.json").write_text(
                '[{"id":"S1","item":"lamp","reserve":40,"budget":55},'
                '{"id":"S2","item":"keyboard","reserve":80,"budget":105},'
                '{"id":"S3","item":"table","reserve":55,"budget":40},'
                '{"id":"S4","item":"monitor","reserve":105,"budget":80}]',
                encoding="utf-8",
            )
            results = base / "results.csv"
            with results.open("w", encoding="utf-8", newline="") as result_file:
                writer = csv.writer(result_file)
                writer.writerow(HEADER)
                writer.writerow(
                    [1, "free", "S1", 1, "deal", 50, 1, 0, 2, 0, 2, ""]
                )
            before = results.read_bytes()

            with patch(
                "run_experiment.ModelClient.complete",
                side_effect=RateLimitError("provider returned HTTP 429"),
            ):
                run_all(base)

            self.assertEqual(results.read_bytes(), before)


class SecretRedactionTests(unittest.TestCase):
    def test_configured_key_is_removed_from_exception_text(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key-value"}):
            clean = redact_secrets("request failed for test-key-value")
        self.assertEqual(clean, "request failed for [REDACTED]")


if __name__ == "__main__":
    unittest.main()
