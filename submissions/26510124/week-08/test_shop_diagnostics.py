"""Synthetic offline evidence only; no credentials, live state or model calls."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import shop_diagnostics as diagnostics

ROOT = Path(__file__).resolve().parent
NOW = 1800000000


class ShopDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".market").mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / ".market")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.json(".market/inventory.json", {"round": 3, "units": []})
        self.json(".market/receipt-reservations.json", {})

    def json(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) + "\n")
        os.utime(path, (NOW, NOW))

    def events(self, rows, name="logs/inventory-seller-events.jsonl"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    def event(self, task="t1", outcome="opened", **changes):
        return {"task": task, "time": NOW - 10, "round": 3, "outcome": outcome, **changes}

    def receipt(self, task="t1", unit="u1", **changes):
        return self.event(task, "receipt_issued", unit=unit, sku="G00001", price=85000, **changes)

    def report(self, **kwargs):
        return diagnostics.build_report(self.root, now=NOW, **kwargs)

    def test_task_dedup_round_time_filter_and_terminal_reasons(self):
        self.events([self.event(), self.event(outcome="quote"), self.event(outcome="quote"),
                     self.event(outcome="rejected", reason_code="price_rejected"),
                     self.event(outcome="rejected", reason_code="price_rejected"),
                     self.event("reserved", "stock_unavailable", reason_code="stock_reserved"),
                     self.event("missing", "stock_unavailable", reason_code="stock_unavailable"),
                     self.event("old", round=2), self.event("unknown", round=None),
                     self.event("ancient", time=NOW - 90000), self.event("future", time=NOW + 1)])
        report = self.report()
        self.assertEqual(report["tasks"]["observed_tasks"], 3)
        self.assertEqual(report["tasks"]["opened_tasks"], 1)
        self.assertEqual(report["tasks"]["tasks_without_open_event"], 2)
        self.assertEqual(report["terminal_reasons"]["price_rejected"], 1)
        self.assertEqual(report["terminal_reasons"]["stock_reserved"], 1)
        self.assertEqual(report["terminal_reasons"]["stock_unavailable"], 1)
        self.assertEqual(report["coverage"]["excluded_seller_rows"]["unknown_round"], 1)

    def test_receipt_alone_is_unconfirmed_never_failed(self):
        self.events([self.event(), self.receipt()])
        report = self.report()
        self.assertEqual(report["tasks"]["settled"], 0)
        self.assertEqual(report["checkout"]["status"], "UNCONFIRMED")
        self.assertIsNone(report["checkout"]["confirmed_failed_tasks"])
        self.assertEqual(sum(report["terminal_reasons"].values()), 0)

    def test_memory_confirmation_requires_matching_terms_and_round(self):
        self.events([self.receipt()])
        sale = {"unit": "u1", "sku": "G00001", "price": 85000, "ledger": 4,
                "receipt_time": NOW - 10, "observed_at": NOW - 5}
        for changes in ({"round": 2}, {"sales": [{**sale, "price": 85001}]},
                        {"sales": [{**sale, "observed_at": NOW + 1}]}):
            self.json(".market/sales-performance.json", {"schema": 1, "round": 3, "sales": [sale], **changes})
            self.assertEqual(self.report()["tasks"]["settled"], 0)
        self.json(".market/sales-performance.json", {"schema": 1, "round": 3, "sales": [sale, sale]})
        self.assertEqual(self.report()["tasks"]["settled"], 1)

    def test_cached_ledger_matching_and_reset_exclusion(self):
        self.events([self.receipt(), self.receipt("earlier", "u0", time=NOW - 100)])
        self.json(".market/market-evidence.json", {"schema": 1, "round": 3, "reset_at": NOW - 50,
                  "events": [{"kind": "settle", "seq": 2, "unit": "u1", "sku": "G00001",
                              "price": 85000, "ts": NOW - 5}]})
        report = self.report()
        self.assertEqual(report["tasks"]["observed_tasks"], 1)
        self.assertEqual(report["tasks"]["settled"], 1)
        self.assertEqual(report["since"], NOW - 50)

    def test_kit_confirmation_and_shop_failure_attempts_separate_from_tasks(self):
        self.events([self.receipt()])
        path = self.root / "logs/market-kit.log"
        path.write_text("12:34:56 [acp] settled G00001 at 85000, ledger #11, unit u1 grade B\n"
                        "12:34:57 [acp] settle refused: fixture\n"
                        "12:34:58 [acp] settle refused: fixture\n"
                        "12:34:59 [acp] payment not completed: buyer-side fixture\n"
                        "12:35:00 [acp] checkout refused: fixture\n")
        report = self.report()
        self.assertEqual(report["tasks"]["settled"], 1)
        self.assertEqual(report["checkout"]["undated_tail_settle_refusal_attempts"], 2)
        self.assertIsNone(report["checkout"]["confirmed_failed_tasks"])

    def test_ambiguous_shared_unit_never_counts_as_multiple_sales(self):
        self.events([self.receipt(), self.receipt("second")])
        (self.root / "logs/market-kit.log").write_text("12:34:56 [acp] settled G00001 at 85000, ledger #11, unit u1 grade B\n")
        report = self.report()
        self.assertEqual(report["tasks"]["settled"], 0)
        self.assertEqual(report["tasks"]["receipt_unconfirmed"], 2)
        self.assertEqual(report["coverage"]["ambiguous_receipt_units"], 1)

    def test_legacy_unknown_reasons_not_invented_and_nonterminal_not_failed(self):
        self.events([self.event("legacy", "stock_unavailable"), self.event("error", "error"),
                     self.event("expired", "expired"), self.event("cancelled", "cancelled"),
                     self.event("request", "invalid_request", reason_code="invalid_request", terminal=False),
                     self.event("model", "error", reason_code="model_error", terminal=True),
                     self.event("inventory", "stock_unavailable", reason_code="inventory_unavailable", terminal=True),
                     self.event("broken", "error", reason_code=["model_error"])])
        report = self.report()
        self.assertEqual(report["terminal_reasons"]["unknown_terminal_reason"], 3)
        self.assertEqual(report["terminal_reasons"]["model_error"], 1)
        self.assertEqual(report["terminal_reasons"]["inventory_unavailable"], 1)
        self.assertEqual(report["terminal_reasons"]["invalid_request"], 0)
        self.assertEqual(report["tasks"]["open_or_missing_end"], 1)

    def test_inventory_holds_do_not_mutate_and_expired_food_excluded(self):
        unit = {"unit": "u1", "sku": "G00001", "kind": "goods", "grade": "A"}
        self.json(".market/inventory.json", {"round": 3, "units": [unit, {**unit, "unit": "u2"},
                  {**unit, "unit": "u3", "kind": "food", "expires_at": NOW - 1}]})
        self.json(".market/receipt-reservations.json", {"u1": "task-secret", "sold-unit": "other"})
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        report = self.report()
        self.assertEqual(report["inventory"]["status"], "fresh")
        self.assertEqual(report["inventory"]["blocked_by_issued_receipt"], 1)
        self.assertEqual(report["inventory"]["blocked_skus"], 1)
        self.assertEqual(report["inventory"]["eligible_not_receipt_held"], 1)
        after = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertNotIn("task-secret", json.dumps(report))
        os.utime(self.root / ".market/inventory.json", (NOW - 60, NOW - 60))
        self.assertEqual(self.report()["inventory"]["status"], "stale")

    def test_bounded_tail_corrupt_partial_and_arbitrary_text_not_emitted(self):
        self.events([self.event(str(i), "error", reason_code="model_error", detail="SECRET-TEXT") for i in range(10)])
        path = self.root / "logs/inventory-seller-events.jsonl"
        with path.open("a") as stream:
            stream.write("bad JSON\n{\"partial\":")
        with patch.object(diagnostics, "MAX_ROWS", 4):
            report = self.report()
        source = report["coverage"]["sources"]["seller_events"]
        self.assertTrue(source["truncated"])
        self.assertTrue(source["partial_last_line"])
        self.assertEqual(source["malformed_rows"], 1)
        self.assertEqual(report["tasks"]["observed_tasks"], 3)
        self.assertNotIn("SECRET-TEXT", json.dumps(report))
        with patch.object(diagnostics, "MAX_BYTES", 400):
            report = self.report()
        self.assertTrue(report["coverage"]["sources"]["seller_events"]["truncated"])

    def test_restock_unknown_round_statuses_and_missing_round_cli(self):
        self.events([self.event(status="planned"), self.event(status="window_closed"), self.event(status="window_invalid"), self.event(status="inventory_changed"),
                     self.event(status="SECRET", detail="SECRET"), self.event(status=[], round=3),
                     self.event(status="refused", round=None)], "logs/restock-decisions.jsonl")
        report = self.report()
        self.assertEqual(report["restock"]["decisions_by_status"], {"planned": 1, "window_closed": 1, "window_invalid": 1, "inventory_changed": 1, "other_status": 2})
        self.assertEqual(report["restock"]["excluded_unknown_round"], 1)
        self.assertNotIn("SECRET", json.dumps(report))
        (self.root / ".market/inventory.json").unlink()
        with self.assertRaisesRegex(ValueError, "--round"):
            self.report()
        self.assertEqual(self.report(rnd=3)["round"], 3)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch.object(diagnostics.time, "time", return_value=NOW):
            self.assertEqual(diagnostics.main(["--root", str(self.root), "--round", "3", "--json"]), 0)
        self.assertEqual(json.loads(out.getvalue())["round"], 3)

    def test_empty_sample_and_invalid_window_do_not_fabricate_rates(self):
        report = self.report()
        self.assertEqual(report["tasks"]["observed_tasks"], 0)
        self.assertNotIn("conversion_rate", report)
        self.assertIn("Shop diagnostics", diagnostics.format_report(report))
        for hours in (0, -1, 169, float("nan"), True):
            with self.assertRaises(ValueError):
                self.report(hours=hours)

    def test_supply_schedule_uses_announced_jitter_and_rejects_stale_state(self):
        inv = {"round": 3, "units": [], "restock": {
            "open": False, "opens_at": NOW + 137, "closes_at": None}}
        self.json(".market/inventory.json", inv)
        self.assertEqual(self.report()["restock"]["schedule"]["seconds_until_open"], 137)
        inv["restock"] = {"open": True, "opens_at": NOW - 30, "closes_at": NOW + 150}
        self.json(".market/inventory.json", inv)
        self.assertEqual(self.report()["restock"]["schedule"]["seconds_remaining"], 150)
        os.utime(self.root / ".market/inventory.json", (NOW - 60, NOW - 60))
        self.assertEqual(self.report()["restock"]["schedule"]["status"], "inventory_unavailable_or_stale")
        inv["restock"]["closes_at"] = NOW - 1
        self.json(".market/inventory.json", inv)
        self.assertEqual(self.report()["restock"]["schedule"]["status"], "schedule_inconsistent")


if __name__ == "__main__":
    unittest.main()
