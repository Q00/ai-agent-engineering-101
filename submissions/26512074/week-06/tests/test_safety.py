"""Local authorization checks, independent of model negotiation choices."""
import asyncio
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness import emit
from market import shop
from protocol import amount, receipt


def completed(price=110, item="lamp"):
    return {"id": "task", "status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [{"parts": [{"data": {"item": item, "price": price}}]}]}


class SafetyTests(unittest.TestCase):
    def test_actual_receipt_required(self):
        self.assertEqual(receipt(completed(), "lamp", 120, 110)["price"], 110)
        for task in ({"status": {"state": "TASK_STATE_INPUT_REQUIRED"}},
                     {"status": {"state": "TASK_STATE_COMPLETED"}, "artifacts": []}):
            with self.assertRaises(ValueError):
                receipt(task, "lamp", 120, 110)

    def test_receipt_cannot_exceed_offer_even_with_unused_budget(self):
        with self.assertRaises(ValueError):
            receipt(completed(115), "lamp", 120, 110)
        with self.assertRaises(ValueError):
            receipt(completed(125), "lamp", 120, 125)

    def test_receipt_identity_and_prior_authority(self):
        with self.assertRaises(ValueError):
            receipt(completed(item="chair"), "lamp", 120, 110)
        with self.assertRaises(ValueError):
            receipt(completed(), "lamp", 120, None)
        task = completed()
        task["artifacts"] *= 2
        with self.assertRaises(ValueError):
            receipt(task, "lamp", 120, 110)

    def test_nonfinite_and_boolean_prices_are_not_money(self):
        for value in (True, "100", -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                amount(value)

    def test_event_is_valid_json_on_legacy_windows_console(self):
        buffer = io.BytesIO()
        output = io.TextIOWrapper(buffer, encoding="cp949")
        with contextlib.redirect_stdout(output):
            emit("unicode_test", message="offer — 🪑 나무")
        output.flush()
        event = json.loads(buffer.getvalue().decode("cp949"))
        self.assertEqual(event["message"], "offer — 🪑 나무")

    def test_existing_ledger_prevents_restart_purchases(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Path(folder) / "ledger.json"
            ledger.write_text('{"state":"unknown"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "ledger already exists"):
                asyncio.run(shop([], [{"id": "one", "item": "lamp"}], 120, ledger))

    def test_duplicate_line_is_rejected_before_network_requests(self):
        with self.assertRaisesRegex(ValueError, "distinct"):
            asyncio.run(shop([], [{"id": "same", "item": "lamp"},
                                  {"id": "same", "item": "chair"}], 300, "unused.json"))


if __name__ == "__main__":
    unittest.main()
