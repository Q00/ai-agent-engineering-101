"""TEST ONLY: simulated decisions/trades verify bounded batch coordination.

These tests run no model, seller process, or external API. Real concurrency is
used only for local ledger/thread scheduling; prices below are explicit test
fixtures and are never installed in the runtime negotiation code.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evidence import EvidenceError, Ledger
import run_challenges
import shopping


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        self.entries = [{"key": "lamp", "item": "lamp", "budget": 120},
                        {"key": "chair", "item": "chair", "budget": 200},
                        {"key": "bookshelf", "item": "bookshelf", "budget": 280}]
        self.directory = [{"id": "test-shop", "url": "http://test-shop",
                           "card": {"name": "TEST ONLY", "skills": [{"id": "home-products"}]}}]

    def test_default_two_workers_overlap_and_complete_three_distinct_purchases(self):
        first_pair = threading.Barrier(2)
        lock = threading.Lock()
        active, peak, first_pair_completed = 0, 0, 0
        starts = []
        limits = []
        prices = {"lamp": 110, "chair": 190, "bookshelf": 270}

        def trade(url, item, budget, **kwargs):
            nonlocal active, peak, first_pair_completed
            with lock:
                active += 1
                peak = max(peak, active)
                starts.append(item)
                if item == "bookshelf":
                    self.assertGreaterEqual(first_pair_completed, 1)
                limits.append(kwargs["timeout"])
            ledger = kwargs["ledger"]
            purchase_id = "test-purchase-" + item
            ledger.reserve(item, prices[item], purchase_id)
            if item in {"lamp", "chair"}:
                first_pair.wait(timeout=3)  # One worker would fail this test.
            receipt = {"item": item, "price": prices[item]}
            ledger.commit(purchase_id, receipt)
            self.assertEqual(kwargs["coordination_context"]["parallel_workers"], 2)
            self.assertEqual(kwargs["coordination_context"]["shared_inference_slots"], 1)
            self.assertEqual(kwargs["coordination_context"]["per_negotiation_seconds"], 280)
            with lock:
                active -= 1
                if item in {"lamp", "chair"}:
                    first_pair_completed += 1
            return {"status": "completed", "receipt": receipt}

        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), patch(
                "shopping.select_shop", return_value=self.directory[0]), patch("shopping.negotiate", side_effect=trade):
            summary = shopping.shop_all(self.directory, self.entries, 600, log_dir=directory)
        self.assertTrue(summary["passed"])
        self.assertEqual(peak, 2)
        self.assertEqual(set(starts), {"lamp", "chair", "bookshelf"})
        self.assertEqual(summary["ledger"]["committed_total"], 570)
        self.assertEqual(len([r for r in summary["ledger"]["purchases"].values() if r["committed"]]), 3)
        self.assertTrue(all(240 < limit <= 280 for limit in limits))
        self.assertEqual(summary["execution_limits"]["concurrent_workers"], 2)

    def test_invalid_limits_fail_before_inference_or_log_creation(self):
        cases = [{"slot_timeout": value} for value in (0, -1, True, "280", float("inf"), float("nan"), 300, 301)]
        cases += [{"timeout": value} for value in (0, True, float("inf"), float("nan"))]
        cases += [{"max_workers": value} for value in (0, -1, True, 1.5)]
        with patch("shopping.Harness") as harness, patch("shopping.Trace") as trace:
            for limits in cases:
                with self.subTest(limits=limits), self.assertRaises(EvidenceError):
                    shopping.shop_all(self.directory, self.entries, 600, log_dir="unused-test-path", **limits)
            harness.assert_not_called()
            trace.assert_not_called()

    def test_each_attempt_uses_remaining_global_deadline_without_changing_the_slot_ceiling(self):
        calls = []

        def trade(url, item, budget, **kwargs):
            calls.append(kwargs)
            self.assertEqual(budget, 120)
            return {"status": "no_deal", "state": "TASK_STATE_FAILED"}

        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), patch(
                "shopping.select_shop", return_value=self.directory[0]) as selector, patch("shopping.negotiate", side_effect=trade):
            summary = shopping.shop_all(self.directory, self.entries[:1], 120, log_dir=directory,
                                        timeout=30, slot_timeout=280)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(0 < call["timeout"] < 30 for call in calls))
        self.assertEqual(selector.call_args_list[0].kwargs["deadline"], selector.call_args_list[1].kwargs["deadline"])
        self.assertEqual(summary["execution_limits"]["overall_timeout"], 30)
        self.assertEqual(summary["allocated_envelopes"][0]["budget"], 120)

    def test_failed_rejected_and_canceled_retry_only_after_actual_slot_release(self):
        for terminal in ("TASK_STATE_FAILED", "TASK_STATE_REJECTED", "TASK_STATE_CANCELED"):
            calls = []

            def trade(url, item, budget, **kwargs):
                calls.append(kwargs)
                ledger = kwargs["ledger"]
                purchase_id = "test-attempt-" + str(len(calls))
                ledger.reserve(item, 110, purchase_id)
                if len(calls) == 1:
                    ledger.release(purchase_id)
                    return {"status": "no_deal", "state": terminal}
                receipt = {"item": item, "price": 110}
                ledger.commit(purchase_id, receipt)
                return {"status": "completed", "receipt": receipt}

            with self.subTest(terminal=terminal), tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(
                    io.StringIO()), patch("shopping.select_shop", return_value=self.directory[0]) as selector, patch(
                    "shopping.negotiate", side_effect=trade):
                summary = shopping.shop_all(self.directory, self.entries[:1], 120, log_dir=directory)
            self.assertTrue(summary["passed"])
            self.assertEqual(len(calls), 2)
            retry = selector.call_args_list[1].kwargs
            self.assertEqual(retry["recent_failure"]["state"], terminal)
            self.assertTrue(retry["coordination_context"]["slot_ledger"]["all_released"])
            self.assertEqual(summary["ledger"]["committed_total"], 110)

    def test_nonterminal_walkaway_round_limit_completion_and_ambiguity_do_not_retry(self):
        cases = [{"status": "no_deal", "state": "TASK_STATE_WORKING"},
                 {"status": "no_deal", "state": "TASK_STATE_INPUT_REQUIRED"},
                 {"status": "no_deal", "reason": "walk away"},
                 {"status": "no_deal", "reason": "round limit"},
                 {"status": "completed"}, {"status": "disputed"},
                 {"status": "failed", "state": "TASK_STATE_FAILED"}]
        for result in cases:
            with self.subTest(result=result), tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(
                    io.StringIO()), patch("shopping.select_shop", return_value=self.directory[0]) as selector, patch(
                    "shopping.negotiate", return_value=result) as trade:
                shopping.shop_all(self.directory, self.entries[:1], 120, log_dir=directory)
            self.assertEqual(trade.call_count, 1)
            self.assertEqual(selector.call_count, 1)

    def test_any_unreleased_or_disputed_record_blocks_terminal_retry(self):
        for terminal in ("TASK_STATE_REJECTED", "TASK_STATE_CANCELED", "TASK_STATE_FAILED"):
            for disputed in (False, True):
                def trade(url, item, budget, **kwargs):
                    ledger = kwargs["ledger"]
                    ledger.reserve(item, 100, "test-old-released")
                    ledger.release("test-old-released")
                    ledger.reserve(item, 110, "test-current")
                    if disputed:
                        ledger.mark_disputed("test-current", "TEST ONLY uncertain receipt")
                    return {"status": "no_deal", "state": terminal}

                with self.subTest(terminal=terminal, disputed=disputed), tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(
                        io.StringIO()), patch("shopping.select_shop", return_value=self.directory[0]) as selector, patch(
                        "shopping.negotiate", side_effect=trade) as counterpart:
                    summary = shopping.shop_all(self.directory, self.entries[:1], 120, log_dir=directory)
                self.assertEqual(counterpart.call_count, 1)
                self.assertEqual(selector.call_count, 1)
                self.assertEqual(summary["ledger"]["reserved_total"], 110)

    def test_reselection_harness_gets_compact_real_ledger_facts_and_exact_terminal_state(self):
        contexts, negotiations, counts = [], [], {}

        def choose(context, **kwargs):
            contexts.append(context)
            return kwargs["validator"]({"action": "select_shop", "item_id": "test-shop",
                "price": None, "offer": None, "reason": "TEST ONLY skill interpretation"})

        def trade(url, item, budget, **kwargs):
            counts[item] = counts.get(item, 0) + 1
            negotiations.append(kwargs["coordination_context"])
            ledger = kwargs["ledger"]
            amount = {"lamp": 110, "chair": 190}[item]
            purchase_id = item + str(counts[item])
            ledger.reserve(item, amount, purchase_id)
            if item == "chair" and counts[item] == 1:
                ledger.release(purchase_id)
                return {"status": "no_deal", "state": "TASK_STATE_REJECTED", "reason": "r" * 400}
            receipt = {"item": item, "price": amount}
            ledger.commit(purchase_id, receipt)
            return {"status": "completed", "receipt": receipt}

        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), patch(
                "shopping.Harness") as harness, patch("shopping.negotiate", side_effect=trade):
            harness.return_value.decide.side_effect = choose
            summary = shopping.shop_all(self.directory, self.entries[:2], 320, log_dir=directory, max_workers=1)
        self.assertTrue(summary["passed"])
        retry = contexts[-1]
        self.assertEqual(retry["recent_failure"]["state"], "TASK_STATE_REJECTED")
        self.assertEqual(len(retry["recent_failure"]["reason"]), 300)
        self.assertTrue(retry["recent_failure"]["reason_truncated"])
        facts = retry["coordination"]
        self.assertEqual(facts["ledger"], {"total_budget": 320, "committed_total": 110,
                                          "reserved_total": 0, "available_budget": 210})
        self.assertEqual(facts["current_budget_ceiling"], 200)
        self.assertEqual(facts["other_completed_purchases"], [{"key": "lamp", "item_id": "lamp", "price": 110}])
        self.assertGreater(facts["remaining_seconds_at_snapshot"], 0)
        self.assertTrue(facts["slot_ledger"]["all_released"])
        self.assertEqual(negotiations[-1]["recent_failure"]["state"], "TASK_STATE_REJECTED")
        self.assertTrue(all(context["purpose"] == "shopping_list" for context in negotiations))

    def test_summary_omits_raw_receipts_history_and_private_fields(self):
        ledger = Ledger(320)
        ledger.reserve("lamp", 110, "other-lamp")
        ledger.commit("other-lamp", {"item": "lamp", "price": 110,
                      "private_reserve": 77, "raw_remote_history": ["do not send this transcript"]})
        ledger.reserve("chair", 180, "active-chair")
        facts = shopping.summarize_coordination(self.entries[1], ledger.snapshot(), deadline=10**12, attempt=1)
        serialized = json.dumps(facts)
        self.assertNotIn("private_reserve", serialized)
        self.assertNotIn("raw_remote_history", serialized)
        self.assertNotIn("do not send this transcript", serialized)
        self.assertFalse(facts["slot_ledger"]["all_released"])

    def test_shopping_cli_propagates_worker_and_negotiation_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "directory.json").write_text(json.dumps(self.directory))
            (root / "list.json").write_text(json.dumps(self.entries))
            with patch("shopping.shop_all", return_value={"passed": True}) as batch, contextlib.redirect_stdout(io.StringIO()):
                shopping.main(["--directory", str(root / "directory.json"), "--list", str(root / "list.json"),
                    "--total-budget", "600", "--workers", "3", "--negotiation-timeout", "275", "--log-dir", str(root / "run")])
        self.assertEqual(batch.call_args.kwargs["max_workers"], 3)
        self.assertEqual(batch.call_args.kwargs["slot_timeout"], 275)

    def test_challenge_runner_passes_limits_to_shopping_and_validates_before_servers(self):
        with tempfile.TemporaryDirectory() as directory, patch("run_challenges.shop_all", return_value={"passed": True}) as batch:
            run_challenges.run_shopping_challenge(self.directory * 20, Path(directory), Mock(), 600,
                                                  max_workers=2, slot_timeout=275)
        self.assertEqual(batch.call_args.kwargs["max_workers"], 2)
        self.assertEqual(batch.call_args.kwargs["slot_timeout"], 275)
        with patch("run_challenges.LocalShops") as servers, self.assertRaises(EvidenceError):
            run_challenges.main(["--challenge", "2", "--negotiation-timeout", "300"])
        servers.assert_not_called()


if __name__ == "__main__":
    unittest.main()
