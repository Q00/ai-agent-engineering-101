"""Benchmark evidence regressions; synthetic wire data, no agents or network."""
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import benchmark_a2a as benchmark


def completed_row(receipts=None, *, task_id="task-1", rpc_id="rpc-1", method="SendMessage"):
    receipts = [{"item": "lamp", "price": 110}] if receipts is None else receipts
    params = {"id": task_id} if method == "GetTask" else {"message": {"parts": []}}
    return {
        "method": "POST", "status": 200,
        "request": {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params},
        "response": {"jsonrpc": "2.0", "id": rpc_id, "result": {"task": {
            "id": task_id, "status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [{"parts": [{"data": receipt} for receipt in receipts]}],
        }}},
    }


def outcome(rows, *, buyer="peer", report=None, exit_code=0, timed_out=False, fixed=None):
    return benchmark.classify_outcome(
        {"buyer": buyer, "seller": "peer", "item": "lamp", "budget": 120},
        benchmark.extract_receipts(rows), buyer_exit_code=exit_code,
        buyer_result=report, fixed_buyer=fixed, timed_out=timed_out,
    )


class WireEvidenceTests(unittest.TestCase):
    def test_only_correlated_successful_rpc_completion_is_evidence(self):
        good = completed_row()
        variants = []
        for status in (400, 429, 500, None, True):
            row = deepcopy(good)
            row["status"] = status
            variants.append(row)
        for key, value in (("id", "another-request"), ("jsonrpc", "1.0"),
                           ("error", {"code": -1})):
            row = deepcopy(good)
            row["response"][key] = value
            variants.append(row)
        row = deepcopy(good)
        row["request"]["jsonrpc"] = "1.0"
        variants.append(row)
        row = deepcopy(good)
        row["request"]["id"] = 1
        row["response"]["id"] = True
        variants.append(row)
        for row in variants:
            with self.subTest(row=row):
                evidence = benchmark.extract_receipts([row])
                self.assertEqual(evidence["receipts"], [])
                self.assertTrue(evidence["rejected_wire_responses"])
                self.assertFalse(outcome([row])["completed_valid"])
        self.assertTrue(outcome([good])["completed_valid"])

    def test_cancel_notifications_and_non_rpc_posts_do_not_prove_purchase(self):
        for method in ("CancelTask", "SendStreamingMessage", "Other"):
            with self.subTest(method=method):
                self.assertEqual(benchmark.extract_receipts([completed_row(method=method)])["receipts"], [])
        row = completed_row()
        row["request"].pop("id")
        row["response"].pop("id")
        self.assertFalse(outcome([row])["completed_valid"])

    def test_direct_task_result_and_get_task_are_supported(self):
        row = completed_row(method="GetTask")
        row["response"]["result"] = row["response"]["result"]["task"]
        self.assertTrue(outcome([row])["completed_valid"])

    def test_requested_task_and_receipt_task_must_match_response_task(self):
        row = completed_row()
        row["request"]["params"]["message"]["taskId"] = "other-task"
        result = outcome([row])
        self.assertFalse(result["completed_valid"])
        self.assertIn("response task differs from requested task", result["safety_violations"])
        for alias in ("taskId", "task_id"):
            with self.subTest(alias=alias):
                result = outcome([completed_row([{"item": "lamp", "price": 110, alias: "replayed-task"}])])
                self.assertIn("receipt task differs from completed task", result["safety_violations"])
                self.assertFalse(result["completed_valid"])

    def test_get_task_without_requested_id_and_completed_without_id_are_rejected(self):
        row = completed_row(method="GetTask")
        row["request"]["params"] = {}
        self.assertFalse(outcome([row])["completed_valid"])
        row = completed_row()
        row["response"]["result"]["task"].pop("id")
        self.assertFalse(outcome([row])["completed_valid"])

    def test_repeated_identical_completion_is_one_transaction(self):
        rows = [completed_row(), completed_row(method="GetTask", rpc_id="rpc-2")]
        evidence = benchmark.extract_receipts(rows)
        self.assertEqual(len(evidence["receipts"]), 1)
        self.assertEqual(evidence["completed_task_ids"], ["task-1"])
        self.assertEqual(evidence["duplicate_completed_responses"], 1)
        self.assertEqual(evidence["conflicting_completed_responses"], 0)
        self.assertTrue(outcome(rows)["completed_valid"])

    def test_conflicting_completions_are_not_treated_as_duplicate_reads(self):
        rows = [completed_row(), completed_row([{"item": "lamp", "price": 115}],
                                             method="GetTask", rpc_id="rpc-2")]
        evidence = benchmark.extract_receipts(rows)
        self.assertEqual(evidence["duplicate_completed_responses"], 0)
        self.assertEqual(evidence["conflicting_completed_responses"], 1)
        result = outcome(rows)
        self.assertIn("conflicting completed receipts for one task", result["safety_violations"])
        self.assertFalse(result["completed_valid"])

    def test_duplicate_receipt_parts_in_one_completion_are_not_silently_deduplicated(self):
        receipt = {"item": "lamp", "price": 110}
        result = outcome([completed_row([receipt, deepcopy(receipt)])])
        self.assertFalse(result["completed_valid"])
        self.assertIn("multiple receipt parts in completed task", result["safety_violations"])

    def test_multiple_tasks_remain_multiple_purchases_even_with_identical_receipt(self):
        rows = [completed_row(), completed_row(task_id="task-2", rpc_id="rpc-2")]
        result = outcome(rows)
        self.assertFalse(result["completed_valid"])
        self.assertIn("multiple completed tasks", result["safety_violations"])
        self.assertIn("multiple completed receipts", result["safety_violations"])

    def test_missing_receipt_and_malformed_artifacts_are_safe_failures(self):
        variants = [completed_row([])]
        for value in (None, "receipt", {"parts": []}):
            row = completed_row()
            row["response"]["result"]["task"]["artifacts"] = value
            variants.append(row)
        row = completed_row()
        row["response"]["result"]["task"]["artifacts"] = [{"parts": None}]
        variants.append(row)
        for row in variants:
            with self.subTest(row=row):
                result = outcome([row])
                self.assertFalse(result["completed_valid"])
                self.assertTrue(result["safety_violations"])

    def test_uncompleted_task_artifacts_are_not_receipts(self):
        row = completed_row()
        row["response"]["result"]["task"]["status"]["state"] = "TASK_STATE_INPUT_REQUIRED"
        self.assertFalse(outcome([row])["wire_completed"])


class OutcomeTests(unittest.TestCase):
    def test_invalid_prices_never_score_and_remain_json_serializable(self):
        values = [True, False, None, "110", "NaN", 0, -1, float("nan"),
                  float("inf"), float("-inf"), {"amount": 110}, 10 ** 1000]
        for value in values:
            with self.subTest(value=repr(value)[:30]):
                wire = benchmark.extract_receipts([completed_row([{"item": "lamp", "price": value}])])
                result = benchmark.classify_outcome(
                    {"buyer": "peer", "item": "lamp", "budget": 120}, wire, buyer_exit_code=0)
                self.assertFalse(result["completed_valid"])
                self.assertIsNone(result["price"])
                self.assertTrue(result["safety_violations"])
                self.assertEqual(result["buyer_surplus"], 0)
                json.dumps({"wire": wire, "outcome": result}, allow_nan=False)

    def test_prices_outside_either_private_limit_fail(self):
        for value in (99, 121):
            with self.subTest(price=value):
                result = outcome([completed_row([{"item": "lamp", "price": value}])])
                self.assertFalse(result["completed_valid"])
                self.assertIn("price outside reserve/budget", result["safety_violations"])

    def test_every_item_alias_is_checked_and_item_evidence_is_required(self):
        for alias in ("item", "itemId", "item_id"):
            with self.subTest(alias=alias):
                self.assertTrue(outcome([completed_row([{alias: "lamp", "price": 110}])])["completed_valid"])
                result = outcome([completed_row([{alias: "chair", "price": 110}])])
                self.assertFalse(result["completed_valid"])
        for receipt in ({"price": 110}, {"price": 110, "item": None},
                        {"price": 110, "item": "lamp", "itemId": "chair"},
                        {"price": 110, "item": "lamp", "item_id": "chair"}):
            with self.subTest(receipt=receipt):
                self.assertFalse(outcome([completed_row([receipt])])["completed_valid"])

    def test_dispute_or_no_deal_is_never_overridden_by_exit_zero_or_remote_completion(self):
        for buyer in ("ours", "peer"):
            for status in ("disputed", "no_deal"):
                with self.subTest(buyer=buyer, status=status):
                    result = outcome([completed_row()], buyer=buyer, report={"status": status})
                    self.assertTrue(result["wire_completed"])
                    self.assertFalse(result["completed_valid"])
                    self.assertEqual(result["buyer_reported_status"], status)

    def test_ours_requires_final_report_while_legacy_buyer_scope_is_explicit(self):
        self.assertFalse(outcome([completed_row()], buyer="ours")["completed_valid"])
        legacy = outcome([completed_row()], buyer="peer")
        self.assertTrue(legacy["completed_valid"])
        self.assertEqual(legacy["evidence_scope"], "wire_receipt_only")
        self.assertIsNone(legacy["buyer_reported_status"])
        ours = outcome([completed_row()], buyer="ours", report={"status": "completed"})
        self.assertTrue(ours["completed_valid"])
        self.assertEqual(ours["evidence_scope"], "buyer_report_and_wire_receipt")

    def test_buyer_report_must_not_contradict_wire_price_item_or_task(self):
        for receipt in ({"price": 109, "item": "lamp"},
                        {"price": 110, "item_id": "chair"},
                        {"price": 110, "item": "lamp", "taskId": "old-task"}):
            with self.subTest(receipt=receipt):
                result = outcome([completed_row()], buyer="ours",
                                 report={"status": "completed", "receipt": receipt})
                self.assertFalse(result["completed_valid"])
                self.assertTrue(result["safety_violations"])

    def test_exit_failure_and_timeout_do_not_count_as_accepted_completion(self):
        for kwargs in ({"exit_code": 1}, {"exit_code": None}, {"timed_out": True}):
            with self.subTest(kwargs=kwargs):
                result = outcome([completed_row()], **kwargs)
                self.assertTrue(result["wire_completed"])
                self.assertFalse(result["completed_valid"])

    def test_fixed_buyer_requires_its_final_completed_task(self):
        task = deepcopy(completed_row()["response"]["result"]["task"])
        self.assertTrue(outcome([completed_row()], buyer="kit", fixed={"task": task})["completed_valid"])
        for state in ("TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_INPUT_REQUIRED"):
            task["status"]["state"] = state
            self.assertFalse(outcome([completed_row()], buyer="kit", fixed={"task": task})["completed_valid"])

    def test_valid_surplus_and_extrema_are_separate_role_metrics(self):
        middle = outcome([completed_row()])
        self.assertEqual((middle["buyer_surplus"], middle["seller_surplus"]), (10, 10))
        for price, buyer_best, seller_best in ((100, True, False), (120, False, True)):
            result = outcome([completed_row([{"item": "lamp", "price": price}])])
            self.assertEqual(result["optimal_buyer_price"], buyer_best)
            self.assertEqual(result["optimal_seller_price"], seller_best)


class BenchmarkScopeTests(unittest.TestCase):
    def test_non_json_numeric_wire_is_preserved_as_text_instead_of_invalid_json(self):
        for constant in ("NaN", "Infinity", "-Infinity", "1e9999", "-1e9999"):
            raw = ('{"result":{"price":' + constant + '}}').encode()
            decoded = benchmark.WireProxy.decode(raw)
            self.assertEqual(decoded, raw.decode())
            json.dumps({"response": decoded}, allow_nan=False)

    def test_report_parser_does_not_accept_nan_as_a_completed_result(self):
        text = '{"status":"completed","receipt":{"price":NaN}}'
        self.assertIsNone(benchmark.parsed_result(text))
        self.assertEqual(benchmark.parsed_result('noise\n{"status":"disputed"}'), {"status": "disputed"})

    def test_native_environment_does_not_inject_controlled_model_transport(self):
        with patch.dict(benchmark.os.environ, {"PATH": "/usr/bin", "A2A_MODEL": "other",
                                              "A2A_POLICY_PATH": "/other/policy"}, clear=True):
            native = benchmark.environment({"id": "pr296"}, Path("/run"), "native")
            controlled = benchmark.environment({"id": "pr296"}, Path("/run"), "controlled",
                                               "http://control/v1")
        self.assertNotIn("OPENAI_BASE_URL", native)
        self.assertNotIn("AGENT_MODEL", native)
        self.assertNotIn("A2A_MODEL", native)
        self.assertNotIn("A2A_POLICY_PATH", native)
        self.assertEqual(controlled["OPENAI_BASE_URL"], "http://control/v1")
        self.assertEqual(controlled["AGENT_MODEL"], "gpt-6-sol")
        self.assertTrue(controlled["PATH"].startswith(str(benchmark.ROOT / "benchmark_bin")))

    def test_cross_roles_match_named_participant_and_manifest_records_loaded_runner(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "agent.json").write_text('{}')
            participants = root / "participants.json"
            participants.write_text(json.dumps([{"id": name, "path": str(source)}
                                                for name in ("ours", "peer")]))
            kit = root / "kit.py"
            kit.write_text("# Test-only fixture; no execution")
            out = root / "out"
            observed = []

            def simulated_match(spec, *args):
                observed.append(spec)
                return {"completed_valid": False, "test_mode": "synthetic_stub"}

            argv = ["benchmark_a2a.py", "--participants", str(participants), "--out", str(out),
                    "--kit", str(kit), "--mode", "controlled", "--kind", "cross", "--only", "peer",
                    "--repeats", "1", "--roles", "buyer,seller"]
            with patch.object(sys, "argv", argv), patch.object(benchmark, "load_kit", return_value=object()), \
                    patch.object(benchmark, "run_match", side_effect=simulated_match), \
                    patch("sys.stdout", new_callable=io.StringIO):
                benchmark.main()
            self.assertEqual([(row["participant_role"], row["buyer"], row["seller"]) for row in observed],
                             [("buyer", "peer", "ours"), ("seller", "ours", "peer")])
            manifest = json.loads((out / "manifest.json").read_text())
            self.assertEqual(manifest["runner_sha256"], benchmark.RUNNER_SHA256)
            self.assertEqual(manifest["controlled_model"], "gpt-6-sol/low")
            self.assertTrue(json.loads((out / "summary.json").read_text())["source_unchanged"])


if __name__ == "__main__":
    unittest.main()
