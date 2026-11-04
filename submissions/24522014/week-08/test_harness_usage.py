"""Usage numbers come from the real command line, and prompt text never counts as a usage-limit error."""

from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import harness_usage as hu

BUYER = "You are a Round 3 buyer maximizing actual surplus. Our model quota is a limit reached rule."
SELLER = "You are a Round 3 inventory sales agent. A buyer is negotiating."


class Usage(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for folder in ("logs/harness", "logs/market-buyer", ".market"):
            (self.root / folder).mkdir(parents=True)
        self.count = 0

    def call(self, prompt=BUYER, *, model="gpt-6.1-sol", label=None, status="ok", error=None, elapsed=4.0, tail="", tokens=800, hour="11"):
        self.count += 1
        path = self.root / "logs/harness" / f"20261104T{hour}00{self.count:02d}000000Z-abc-codex"
        path.mkdir()
        metadata = {"model": label or model, "status": status, "elapsed_seconds": elapsed,
                    "command": ["codex.exe", "exec", "--model", model, "-"]}
        if error:
            metadata["error"] = error
        (path / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        (path / "request.txt").write_text(prompt, encoding="utf-8")
        (path / "stderr.txt").write_text(f"user\n{prompt}\ncodex\n{tail}\ntokens used\n{tokens:,}\n", encoding="utf-8")

    def report(self, since="20261104T000000000000Z", until=None):
        return hu.report(self.root, since, until)

    def test_role_and_actual_model_come_from_the_prompt_and_command(self):
        self.call(BUYER, model="gpt-6.1-sol", elapsed=2)
        self.call(BUYER, model="gpt-6.1-sol", elapsed=6, tokens=1200)
        self.call(SELLER, model="gpt-6-luna", elapsed=3)
        self.call(BUYER, model="gpt-6.1-sol", label="gpt-6-luna")
        rows = self.report()["by_role_and_actual_model"]
        buyer = rows["buyer|gpt-6.1-sol"]
        self.assertEqual((buyer["calls"], buyer["elapsed_median"], buyer["tokens_sum"]), (3, 4.0, 2800))
        self.assertEqual(buyer["label_differs_from_actual"], 1)
        self.assertEqual(rows["seller|gpt-6-luna"]["calls"], 1)

    def test_prompt_words_are_not_a_limit_error_and_real_ones_are(self):
        self.call(BUYER)
        self.call(BUYER, status="failed", error="Harness exited with code 1; see stderr.txt", tail="ERROR: unexpected status 429 Too Many Requests")
        self.call(BUYER, status="failed", error="Harness exited with code 1; see stderr.txt", tail="You have hit your usage limit")
        self.call(BUYER, status="failed", error="Expected exactly action, price, reason in one JSON object", tail="not json")
        result = self.report()
        self.assertEqual(result["usage_limit_errors"], 2)
        self.assertEqual(result["by_role_and_actual_model"]["buyer|gpt-6.1-sol"]["failed"], 3)
        self.assertTrue(result["rollback_signal"])

    def test_clean_run_has_no_rollback_signal(self):
        self.call(BUYER)
        self.call(SELLER)
        self.assertFalse(self.report()["rollback_signal"])

    def test_seller_failure_is_a_rollback_signal_and_timeouts_are_counted(self):
        self.call(SELLER, status="failed", error="Harness exceeded 90 seconds", elapsed=90)
        result = self.report()
        self.assertEqual(result["seller_failures"]["harness_calls_failed"], 1)
        self.assertEqual(result["by_role_and_actual_model"]["seller|gpt-6.1-sol"]["timeouts"], 1)
        self.assertTrue(result["rollback_signal"])

    def test_agy_calls_are_counted_by_provider_with_their_own_tokens(self):
        self.call(BUYER, model="gemini-3.8-flash-low", tokens=1)
        path = sorted((self.root / "logs/harness").iterdir())[-1]
        metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
        metadata.update(provider="agy", tokens=9000)
        (path / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        result = self.report()
        self.assertEqual(result["calls_by_provider"], {"agy": 1})
        row = result["by_role_and_actual_model"]["buyer|gemini-3.8-flash-low"]
        self.assertEqual((row["provider"], row["tokens_sum"]), ("agy", 9000))

    def test_window_and_busiest_hour(self):
        self.call(hour="10")
        self.call(hour="11")
        self.call(hour="11")
        row = self.report("20261104T110000000000Z")["by_role_and_actual_model"]["buyer|gpt-6.1-sol"]
        self.assertEqual((row["calls"], row["max_calls_in_one_utc_hour"]), (2, 2))
        self.assertEqual(self.report(until="20261104T110000000000Z")["by_role_and_actual_model"]["buyer|gpt-6.1-sol"]["calls"], 1)

    def test_kit_log_dates_are_recovered_across_midnight(self):
        (self.root / "logs/market-kit.log").write_text(
            "23:59:00 [seller] [harness] decision failed: a\n00:01:00 [x] ok\n10:00:00 [seller] [harness] decision failed: b\n",
            encoding="utf-8")
        self.assertEqual(hu.seller_failures(self.root, "20000101T000000000000Z", today=date(2026, 11, 4)), 2)
        self.assertEqual(hu.seller_failures(self.root, "20261103T230000000000Z", today=date(2026, 11, 4)), 1)

    def test_trade_span_uses_the_folder_and_record_stamps(self):
        (self.root / "logs/market-buyer/20261104T110030000000Z-a.json").write_text(json.dumps(
            {"record": {"outcome": "no_deal", "buyer_log_dir": "C:\\x\\20261104T110000000000Z-b", "decision_trail": []}}), encoding="utf-8")
        (self.root / "logs/market-buyer/20261104T120000000000Z-a.json").write_text(json.dumps(
            {"record": {"outcome": "research_skipped"}}), encoding="utf-8")
        hooks = hu.report(self.root, "20261104T000000000000Z")["hooks"]
        self.assertEqual((hooks["hook_runs"], hooks["trades"], hooks["trade_seconds_median"]), (2, 1, 30.0))


if __name__ == "__main__":
    unittest.main()
