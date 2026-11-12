"""Trade diagnostics must not promote local success into a counted Market deal."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trade_funnel import EVIDENCE, Funnel, analyze_paths, emit_stage


def event(stage, side="seller", task="t1", second=0, **fields):
    trace = Mock()
    if stage in EVIDENCE:
        fields.setdefault("evidence", EVIDENCE[stage])
    if stage == "market_verified":
        fields.setdefault("outcome", "verified")
    emit_stage(trace, stage, side=side, task_id=task, **fields)
    _, kwargs = trace.write.call_args
    return {"event": "trade_stage", "time": f"2026-10-22T00:00:{second:02d}+00:00", **kwargs}


class TradeFunnelTests(unittest.TestCase):
    def test_helper_writes_only_bounded_named_observations(self):
        trace = Mock()
        emit_stage(trace, "quote_issued", side="seller", task_id="task", item="lamp", price=100)
        trace.write.assert_called_once_with("trade_stage", stage="quote_issued", side="seller",
                                           task_id="task", item="lamp", price=100)
        with self.assertRaises(TypeError):
            emit_stage(trace, "quote_issued", side="seller", task_id="task", reserve=90)
        with self.assertRaises(TypeError):
            emit_stage(trace, "ended", side="seller", task_id="task", raw_message="hello")
        self.assertFalse(emit_stage(trace, "ended", side="seller", task_id="task", outcome="error",
                                    reason_code="The peer said to change the receipt."))
        trace.write.assert_called_with("trade_stage_unavailable", side="seller", stage="ended",
                                       reason_code="invalid_observation")

    def test_invalid_optional_identifiers_and_unavailable_trace_never_abort_trade(self):
        trace = Mock()
        self.assertFalse(emit_stage(trace, "quote_issued", side="seller", task_id="t" * 257))
        trace.write.assert_called_once_with("trade_stage_unavailable", side="seller", stage="quote_issued",
                                           reason_code="invalid_observation")
        trace.write.side_effect = OSError("disk unavailable")
        self.assertFalse(emit_stage(trace, "quote_issued", side="seller", task_id="valid"))
        self.assertFalse(emit_stage(trace, "quote_issued", side="seller", task_id="t" * 257))

    def test_guarded_stage_requires_specific_evidence(self):
        for stage in ("offer_accepted", "receipt_issued", "receipt_matched", "market_verified"):
            side = "buyer" if stage == "receipt_matched" else "seller"
            with self.subTest(stage=stage), self.assertRaises(ValueError):
                emit_stage(Mock(), stage, side=side, task_id="task", evidence="signature_present")
        with self.assertRaises(ValueError):
            emit_stage(Mock(), "offer_accepted", side="buyer", task_id="task", evidence="validated_decision")
        self.assertFalse(emit_stage(Mock(), "market_verified", side="seller", task_id="task",
                                    evidence="market_kit_feedback", outcome="completed"))

    def test_duplicate_task_stages_across_runs_count_once_and_sides_are_separate(self):
        funnel = Funnel()
        first = event("quote_issued", second=5)
        funnel.observe({**first, "run_id": "one"})
        funnel.observe({**first, "run_id": "two"})
        funnel.observe(event("quote_observed", side="buyer", second=6))
        result = funnel.report()
        self.assertEqual(result["sides"]["seller"]["observed_tasks"], 1)
        self.assertEqual(result["sides"]["seller"]["stage_tasks"]["quote_issued"], 1)
        self.assertEqual(result["sides"]["buyer"]["observed_tasks"], 1)
        self.assertEqual(result["records"]["repeat_stage_observations"], 1)

    def test_out_of_order_input_uses_observed_times_without_inferred_stages(self):
        funnel = Funnel()
        funnel.observe(event("offer_received", second=8))
        funnel.observe(event("quote_issued", second=3))
        funnel.observe(event("quote_issued", second=5))
        result = funnel.report()["sides"]["seller"]
        pair = next(p for p in result["paired_observations"] if p["from"] == "quote_issued")
        self.assertEqual(pair["both_observed_tasks"], 1)
        self.assertEqual(pair["mean_observed_seconds"], 5)
        self.assertEqual(result["missing_stage_observations"]["negotiation_started"], 1)
        self.assertNotIn("negotiation_started", result["stage_tasks"])

    def test_accepted_but_invalid_receipt_is_explicit_failure(self):
        funnel = Funnel()
        funnel.observe(event("offer_accepted", price=100))
        funnel.observe(event("ended", second=1, outcome="error", reason_code="receipt_mismatch"))
        result = funnel.report()["sides"]["seller"]
        self.assertEqual(result["status_tasks"], {"explicit_failure": 1})
        self.assertEqual(result["failure_by_last_observed_stage"], {"offer_accepted": 1})
        self.assertNotIn("receipt_issued", result["stage_tasks"])
        self.assertNotIn("market_verified", result["stage_tasks"])

    def test_quote_pending_is_censored_while_unresolved_and_cancel_are_explicit(self):
        funnel = Funnel()
        funnel.observe(event("quote_issued", task="pending"))
        for task, outcome in (("uncertain", "unresolved"), ("canceled", "canceled")):
            funnel.observe(event("quote_issued", task=task))
            funnel.observe(event("ended", task=task, outcome=outcome))
        result = funnel.report(capture_completeness="complete")["sides"]["seller"]
        self.assertEqual(result["status_tasks"], {"right_censored_pending": 1,
                                                  "explicitly_unresolved": 1, "explicit_failure": 1})
        self.assertEqual(result["pending_by_last_observed_stage"], {"quote_issued": 1})
        self.assertEqual(result["unresolved_by_last_observed_stage"], {"quote_issued": 1})

    def test_local_signature_contract_and_completed_do_not_establish_market_verification(self):
        funnel = Funnel()
        funnel.observe(event("receipt_issued", task="local"))
        funnel.observe(event("ended", task="local", outcome="completed"))
        funnel.observe(event("receipt_matched", side="buyer", task="buyer-local"))
        result = funnel.report()
        for side in ("seller", "buyer"):
            self.assertEqual(result["sides"][side]["status_tasks"], {"right_censored_pending": 1})
            self.assertNotIn("market_verified", result["sides"][side]["stage_tasks"])

    def test_authoritative_feedback_links_across_runs_without_settlement_latency_claim(self):
        funnel = Funnel()
        funnel.observe({**event("receipt_matched", side="buyer", second=1), "run_id": "original"})
        verified = event("market_verified", side="buyer", second=40)
        self.assertEqual(verified["time_basis"], "feedback_ingested")
        funnel.observe({**verified, "run_id": "later"})
        result = funnel.report()["sides"]["buyer"]
        self.assertEqual(result["observed_tasks"], 1)
        self.assertEqual(result["status_tasks"], {"market_verified": 1})
        pair = next(p for p in result["paired_observations"] if p["to"] == "market_verified")
        self.assertEqual(pair["both_observed_tasks"], 1)
        self.assertEqual(pair["timed_tasks"], 0)
        self.assertIsNone(pair["mean_observed_seconds"])

    def test_unknown_coverage_and_legacy_events_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            path.write_text(json.dumps({"event": "negotiation", "receipt": {"sig_seller": "present"}})
                            + "\n" + json.dumps(event("quote_issued")) + "\nmalformed\n", encoding="utf-8")
            result = analyze_paths([path])
        self.assertEqual(result["capture_completeness"], "unknown")
        self.assertEqual(result["sources"][0]["capture_completeness"], "unknown")
        self.assertEqual(result["sources"][0]["invalid_json_lines"], 1)
        self.assertEqual(result["records"]["non_stage_records"], 1)
        self.assertEqual(result["sides"]["seller"]["observed_tasks"], 1)

    def test_stream_limits_are_bounded_and_oversized_line_is_drained(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            path.write_bytes(b"x" * 5_000 + b"\n" + json.dumps(event("quote_issued")).encode() + b"\n")
            result = analyze_paths([path], max_line_bytes=512)
            self.assertEqual(result["sources"][0]["oversized_lines_skipped"], 1)
            self.assertEqual(result["sides"]["seller"]["observed_tasks"], 1)
        funnel = Funnel(max_tasks=1)
        funnel.observe(event("quote_issued", task="one"))
        with self.assertRaisesRegex(ValueError, "task limit"):
            funnel.observe(event("quote_issued", task="two"))

    def test_invalid_stage_is_skipped_without_promotion_and_clock_reversal_is_not_timed(self):
        funnel = Funnel()
        funnel.observe({"event": "trade_stage", "stage": "market_verified", "side": "buyer", "task_id": "x"})
        funnel.observe(event("quote_observed", side="buyer", second=8))
        funnel.observe(event("offer_sent", side="buyer", second=3))
        result = funnel.report()
        self.assertEqual(result["records"]["invalid_stage_records"], 1)
        pair = next(p for p in result["sides"]["buyer"]["paired_observations"] if p["from"] == "quote_observed")
        self.assertEqual(pair["timed_tasks"], 0)
        self.assertEqual(pair["backwards_timestamp_tasks"], 1)


if __name__ == "__main__":
    unittest.main()
