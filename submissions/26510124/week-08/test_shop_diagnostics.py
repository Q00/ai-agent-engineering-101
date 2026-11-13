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
    def test_checkout_events_require_receipt_identity_and_time(self):
        receipt = self.receipt()
        self.events([receipt])
        event = {"time": receipt["time"] + 1, "task": receipt["task"], "unit": receipt["unit"],
                 "stage": "settlement_confirmed", "ledger": 9}
        for bad in ({"unit":"wrong"}, {"task":"wrong"}, {"time":receipt["time"]-1}, {"ledger":True}):
            pipeline, confirmed = diagnostics.checkout_summary([{**event, **bad}], [receipt], NOW)
            self.assertFalse(confirmed)
        self.events([{**event, "stage":"checkout_created", "ledger":None}, event, event],
                    ".market/checkout-events.jsonl")
        report = self.report()
        self.assertEqual(report["tasks"]["settled"], 1)
        self.assertEqual(report["checkout_pipeline"]["matched_tasks_by_stage"],
                         {"checkout_created":1, "settlement_confirmed":1})

    def test_response_latency_is_unique_and_unknown_is_not_zero(self):
        events = [self.event("one", "quote", first_response_seconds=2.5, stage="opening"),
                  self.event("one", "cancelled", first_response_seconds=2.5, stage="negotiate"),
                  self.event("old", "quote", round=2, first_response_seconds=999),
                  self.event("unknown", "opened")]
        result = diagnostics.response_summary(events, 3, NOW, NOW-3600)
        self.assertEqual(result["first_response_samples"], 1)
        self.assertEqual(result["first_response_p95_seconds"], 2.5)
        self.assertEqual(result["last_stage_by_task"], {"negotiate":1, "unknown":1})

    def test_empty_plan_is_not_a_purchase_and_invalid_legacy_plan_stays_unknown(self):
        self.events([
            {"time": NOW - 30, "round": 3, "status": "planned", "buy": []},
            {"time": NOW - 20, "round": 3, "status": "planned", "buy": [{"sku": "G12345678", "qty": 1}]},
            {"time": NOW - 15, "round": 3, "status": "planned"},
            {"time": NOW - 10, "round": 3, "status": "planned", "buy": [{"sku": "PRIVATE", "qty": True}]},
            {"time": NOW - 5, "round": 3, "status": "already_planned", "buy": []},
            {"time": NOW - 5, "round": 2, "status": "planned", "buy": []},
        ], "logs/restock-decisions.jsonl")
        report = self.report()
        self.assertEqual(report["restock"]["plans_by_contents"], {"empty": 1, "nonempty": 1, "unknown_or_invalid": 2})
        output = diagnostics.format_report(report)
        self.assertIn("not confirmed purchases", output)
        self.assertNotIn("PRIVATE", output)

    def test_expired_evidence_and_repeated_timeout_remain_visible_without_private_errors(self):
        self.json(".market/market-evidence.json", {"schema": 1, "round": 3,
            "updated_at": NOW - 4500, "events": [], "market_stock": {
                "round": 3, "observed_at": NOW - 4500, "shop_names_by_url": {"PRIVATE": "PRIVATE"}}})
        self.events([
            {"time": NOW - seconds, "round": 3, "status": "unavailable", "error": "ReadTimeout"}
            for seconds in (2500, 1500, 500)
        ] + [
            {"time": NOW - 4000, "round": 3, "status": "unavailable", "error": "ReadTimeout"},
            {"time": NOW - 10, "round": 2, "status": "unavailable", "error": "ReadTimeout"},
            {"time": NOW - 5, "round": 3, "status": "unavailable", "error": "PRIVATE-ERROR", "detail": "PRIVATE-TOKEN"},
        ], "logs/market-evidence.jsonl")
        report = self.report(hours=1)
        evidence = report["restock"]["market_evidence"]
        self.assertEqual(evidence["cache_status"], "stale_or_clock_changed")
        self.assertEqual(evidence["cache_age_seconds"], 4500)
        self.assertEqual(evidence["refresh_failures_by_error"], {"ReadTimeout": 3, "other_error": 1})
        self.assertFalse(evidence["stock_snapshot_fresh"])
        self.assertEqual(evidence["named_shop_urls"], 0)
        self.assertNotIn("PRIVATE", diagnostics.format_report(report))

    def test_missing_evidence_is_distinct_from_fresh_empty_sample_and_missing_identity(self):
        self.assertEqual(self.report()["restock"]["market_evidence"]["cache_status"], "missing")
        self.json(".market/market-evidence.json", {"schema": 1, "round": 3,
            "updated_at": NOW, "events": [], "market_stock": {"round": 3, "observed_at": NOW}})
        evidence = self.report()["restock"]["market_evidence"]
        self.assertEqual(evidence["cache_status"], "fresh")
        self.assertEqual(evidence["cached_fact_rows"], 0)
        self.assertTrue(evidence["stock_snapshot_fresh"])
        self.assertEqual(evidence["named_shop_urls"], 0)

    def test_unknown_round_restock_errors_are_visible_without_private_detail(self):
        self.events([
            {"time": NOW - 10, "status": "refused", "error": "ValueError", "detail": "PRIVATE-TOKEN"},
            {"time": NOW - 5, "status": "refused", "error": "PRIVATE-ERROR", "detail": "secret"},
            {"time": NOW - 90000, "status": "refused", "error": "ValueError"},
            {"time": NOW + 1, "status": "refused", "error": "ValueError"},
            {"time": NOW - 3, "round": 2, "status": "refused", "error": "ValueError"},
        ], "logs/restock-decisions.jsonl")
        report = self.report()
        self.assertEqual(report["restock"]["decisions_by_status"], {})
        self.assertEqual(report["restock"]["unknown_round_refusals_by_error"], {"ValueError": 1, "other_error": 1})
        text = diagnostics.format_report(report)
        self.assertIn("Restock rows without a known round: 2", text)
        self.assertIn("excluded from current-round counts", text)
        self.assertNotIn("PRIVATE", text)

    def test_market_clock_only_changes_supply_schedule_not_local_log_window(self):
        self.json(".market/inventory.json", {"round": 3, "units": [], "restock": {
            "open": True, "opens_at": NOW - 650, "closes_at": NOW - 470}})
        self.events([self.event()])
        with patch.object(diagnostics, "schedule_now", return_value={
                "now": NOW - 600, "status": "synced", "offset_seconds": -600,
                "uncertainty_seconds": 1}):
            report = self.report(hours=1)
        self.assertEqual(report["restock"]["schedule"]["status"], "open")
        self.assertEqual(report["restock"]["schedule"]["seconds_remaining"], 130)
        self.assertEqual(report["inventory"]["status"], "fresh")
        self.assertEqual(report["tasks"]["opened_tasks"], 1)
        self.assertEqual(report["since"], NOW - 3600)
        self.assertIn('"status": "synced"', diagnostics.format_report(report))

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

    def test_lifecycle_counts_deadlines_without_mutation_or_private_output(self):
        rows = {}
        for index, status in enumerate(diagnostics.LEASE_STATUSES):
            rows[f"private-task-{index}"] = {
                "unit": f"private-unit-{index}", "status": status,
                "issued_at": NOW - 300, "deadline": NOW - 1,
                "receipt": {"price": 987654321, "sig_seller": "PRIVATE-SIGNATURE"},
                "session_id": f"private-session-{index}"}
        self.json(".market/receipt-leases.json", {"version": 1, "leases": rows})
        self.json(".market/receipt-reservations.json", {
            **{row["unit"]: task for task, row in rows.items()}, "legacy-unit": "legacy-task"})
        before = {p.relative_to(self.root): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in self.root.rglob("*") if p.is_file()}
        report = self.report()
        lifecycle = report["receipt_lifecycle"]
        self.assertEqual(lifecycle["status"], "snapshot")
        self.assertEqual(lifecycle["legacy_holds"], 1)
        self.assertEqual({s: lifecycle[s] for s in diagnostics.LEASE_STATUSES},
                         dict.fromkeys(diagnostics.LEASE_STATUSES, 1))
        self.assertEqual(lifecycle["deadline_passed_issued_or_checkout"], 2)
        self.assertIsNone(report["checkout"]["confirmed_failed_tasks"])
        after = {p.relative_to(self.root): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        output = json.dumps(report) + diagnostics.format_report(report)
        for private in ("private-task", "private-unit", "private-session", "PRIVATE-SIGNATURE", "987654321"):
            self.assertNotIn(private, output)
        self.assertIn("release is not confirmed", output)
        self.assertIn("Settling or uncertain does not mean unpaid or failed", output)

    def test_missing_and_unreadable_lifecycle_do_not_invent_zero_pending_payments(self):
        self.json(".market/receipt-reservations.json", {"legacy-unit": "legacy-task"})
        lifecycle = self.report()["receipt_lifecycle"]
        self.assertEqual(lifecycle["status"], "legacy_no_lifecycle")
        self.assertEqual(lifecycle["legacy_holds"], 1)
        self.assertIsNone(lifecycle["settling"])
        self.assertIsNone(lifecycle["uncertain"])
        path = self.root / ".market/receipt-leases.json"
        path.write_text("{broken")
        report = self.report()
        self.assertEqual(report["receipt_lifecycle"]["status"], "unavailable")
        self.assertIsNone(report["receipt_lifecycle"]["legacy_holds"])
        self.assertEqual(report["coverage"]["sources"]["receipt-leases"]["status"], "unreadable")
        self.json(".market/receipt-leases.json", {"version": 1, "leases": {}, "padding": "x" * 1000})
        with patch.object(diagnostics, "MAX_BYTES", 200):
            report = self.report()
        self.assertEqual(report["receipt_lifecycle"]["status"], "unavailable")
        self.assertEqual(report["coverage"]["sources"]["receipt-leases"]["status"], "oversized")

    def test_lifecycle_invalid_rows_and_mismatched_holds_stay_unknown(self):
        row = {"unit": "u1", "status": "issued", "issued_at": NOW - 10, "deadline": NOW + 290}
        for changes in ({"status": ["issued"]}, {"deadline": float("nan")},
                        {"deadline": NOW - 11}, {"status": "SECRET-STATUS"}):
            self.json(".market/receipt-leases.json", {"version": 1, "leases": {"task": {**row, **changes}}})
            report = self.report()
            self.assertEqual(report["receipt_lifecycle"]["status"], "invalid_lifecycle")
            self.assertIsNone(report["receipt_lifecycle"]["issued"])
            self.assertNotIn("SECRET-STATUS", json.dumps(report))
        self.json(".market/receipt-leases.json", {"version": 1, "leases": {"task": row}})
        self.json(".market/receipt-reservations.json", {"other-unit": "task"})
        lifecycle = self.report()["receipt_lifecycle"]
        self.assertEqual(lifecycle["status"], "snapshot_hold_mismatch")
        self.assertIsNone(lifecycle["legacy_holds"])
        self.assertEqual(lifecycle["issued"], 1)
        self.assertEqual(lifecycle["deadline_passed_issued_or_checkout"], 0)

    def test_legacy_recovery_revocation_is_not_a_completed_or_failed_payment(self):
        self.json(".market/receipt-leases.json", {"version": 1, "leases": {"legacy-task": {
            "receipt": None, "unit": "u1", "issued_at": NOW, "deadline": NOW,
            "status": "revoked", "session_id": None, "reason": "legacy_restart_recovery"}}})
        report = self.report()
        self.assertEqual(report["receipt_lifecycle"]["revoked"], 1)
        self.assertEqual(report["receipt_lifecycle"]["completed"], 0)
        self.assertEqual(report["receipt_lifecycle"]["legacy_holds"], 0)
        self.assertIsNone(report["checkout"]["confirmed_failed_tasks"])

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
