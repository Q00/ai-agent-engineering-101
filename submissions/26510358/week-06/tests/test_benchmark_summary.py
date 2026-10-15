"""Isolated aggregation fixtures; no fixture is written into real benchmark logs."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import benchmark_summary as summaries


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def row(name="match-1", *, buyer="ours", seller="peer", participant="peer", price=110, valid=True):
    return {"id": name, "buyer": buyer, "seller": seller, "participant": participant,
            "item": "lamp", "budget": 120, "repeat": 1, "completed_valid": valid,
            "price": price, "buyer_surplus": 120 - price if valid else 0,
            "seller_surplus": price - 100 if valid else 0,
            "elapsed_seconds": 4, "rpc_calls": 2, "buyer_exit_code": 0,
            "safety_violations": []}


def run(root, rows, *, mode="native", kind="cross", attempts=None):
    save(root / "manifest.json", {"arguments": {"mode": mode, "kind": kind},
                                  "participants": {"peer": {"source_commit": "peer-commit"},
                                                   "ours": {"source_commit": "our-commit"}},
                                  "source_hashes": {"peer": {"seller.py": "source-digest"}},
                                  "runner_sha256": "runner-digest", "claim_scope": "limited observations",
                                  "claim_limits": ["small sample"], "controlled_model": "gpt-6-sol/low"})
    path = root / "summary.json"
    save(path, {"mode": mode, "kind": kind, "results": rows,
                "attempts": len(rows) if attempts is None else attempts, "source_unchanged": True,
                "runner_sha256": "runner-digest", "limitations": "No ranking"})
    return path


class SummaryTests(unittest.TestCase):
    def test_actual_cross_roles_override_historical_label_and_provenance_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seller = row()
            seller["participant_role"] = "buyer"  # Historical runner's reversed label.
            buyer = row("match-2", buyer="peer", seller="ours", price=100)
            report = summaries.summarize([run(root, [seller, buyer])])
            groups = {group["actual_role"]: group for group in report["groups"]}
            self.assertEqual(set(groups), {"buyer", "seller"})
            self.assertEqual(groups["seller"]["our_role"], "buyer")
            self.assertEqual(groups["seller"]["pairing"], "our_buyer_vs_peer_seller")
            self.assertEqual(groups["buyer"]["pairing"], "peer_buyer_vs_our_seller")
            self.assertTrue(groups["seller"]["individual_results"][0]["role_label_disagrees"])
            self.assertEqual(groups["seller"]["participant_commit"], "peer-commit")
            self.assertEqual(groups["seller"]["participant_source_sha256"], {"seller.py": "source-digest"})
            self.assertEqual(groups["seller"]["runner_sha256"], "runner-digest")
            self.assertEqual(report["runs"][0]["manifest_claim_limits"], ["small sample"])
            self.assertEqual(report["runs"][0]["summary_limitations"], "No ranking")

    def test_native_controlled_items_and_runs_never_pool_and_synthetic_rows_are_excluded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real = row(price=110)
            synthetic = row("synthetic", price=100)
            synthetic["test_mode"] = "simulated_stub"
            first = run(root / "first", [real, synthetic])
            controlled = run(root / "controlled", [row(price=115)], mode="controlled")
            report = summaries.summarize([first, first, controlled])
            self.assertEqual(len(report["runs"]), 2)
            self.assertEqual(len(report["groups"]), 2)
            self.assertEqual([group["attempts"] for group in report["groups"]], [1, 1])
            self.assertEqual([group["prices"]["mean"] for group in report["groups"]], [110, 115])
            self.assertEqual(len(report["runs"][0]["excluded_synthetic"]), 1)

    def test_missing_outcomes_and_surplus_are_unknown_not_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = row("unknown")
            for field in ("completed_valid", "buyer_surplus", "seller_surplus", "elapsed_seconds", "rpc_calls"):
                missing.pop(field)
            report = summaries.summarize([run(root, [row(), missing])])
            group = report["groups"][0]
            self.assertEqual(group["attempts"], 2)
            self.assertIsNone(group["completed_valid"])
            self.assertEqual(group["observed_completed_valid"], 1)
            self.assertEqual(group["unknown_outcomes"], 1)
            self.assertIsNone(group["surplus_per_attempt"])
            self.assertEqual(group["elapsed_seconds"], {"mean": 4.0, "observed": 1, "missing": 1})
            self.assertIsNone(group["model_calls"]["request_count"])

    def test_checkpoint_missing_summary_and_mismatched_counts_have_no_outcome_totals(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run(root, [row()])
            (root / "summary.json").unlink()
            save(root / "checkpoint-001.json", [row()])
            for path in (root, root / "summary.json", root / "checkpoint-001.json"):
                with self.subTest(path=path):
                    report = summaries.summarize([path])
                    self.assertEqual(report["groups"], [])
                    self.assertEqual(report["runs"][0]["status"], "checkpointing")
                    self.assertNotIn("completed_valid", report["runs"][0])
            report = summaries.summarize([run(root, [row()], attempts=3)])
            self.assertEqual(report["groups"], [])
            self.assertEqual(report["runs"][0]["status"], "checkpointing")

    def test_failures_contribute_only_explicit_surplus_and_errors_are_visible(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bad = row("failed", valid=False)
            bad.update(error="buyer failed", safety_violations=["wrong item"])
            report = summaries.summarize([run(root, [row(), bad])])
            group = report["groups"][0]
            self.assertEqual(group["completed_valid"], 1)
            self.assertEqual(group["prices"]["all_individual"], [{"match_id": "match-1", "repeat": 1, "price": 110}])
            self.assertEqual(group["surplus_per_attempt"], 5)
            self.assertEqual(group["safety_affected_attempts"], 1)
            self.assertEqual(group["errors"][0]["error"], "buyer failed")

    def test_model_attempts_are_role_scoped_and_http_remains_global(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = run(root / "run", [row()], mode="controlled")
            traces = path.parent / "match-1"
            buyer = traces / "buyer-trace" / "buyer.jsonl"
            buyer.parent.mkdir(parents=True)
            buyer.write_text('\n'.join(json.dumps({"event": event, "role": "buyer"})
                                        for event in ["harness_request", "harness_request", "harness_response"]))
            cli = traces / "transport" / "call-1"
            save(cli / "request.json", {"source": "claude-cli", "mode": "controlled_same_model_rehost_not_original_provider"})
            save(cli / "process.json", {"pid": 123})
            save(cli / "result.json", {"status": "completed"})
            http = root / "shared" / "call-http"
            save(http / "request.json", {"id": "one-http", "source": "openai-compatible-http",
                                         "mode": "controlled_same_model_rehost_not_original_provider"})
            save(http / "result.json", {"status": "failed"})
            report = summaries.summarize([path], shared_http_traces=[http.parent, http.parent])
            calls = report["groups"][0]["model_calls"]
            self.assertEqual(calls["request_count"], 1)  # Participant is the seller.
            self.assertEqual(calls["roles"]["buyer"]["request_count"], 2)
            self.assertEqual(calls["roles"]["seller"]["request_count"], 1)
            self.assertEqual(report["shared_http_calls"]["request_count"], 1)
            self.assertEqual(report["shared_http_calls"]["scope"], "global_only")

    def test_output_is_exclusive_and_console_contains_real_role_pairing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = run(root / "run", [row()])
            out = root / "report.json"
            with patch("sys.stdout", new_callable=io.StringIO) as console:
                summaries.main([str(source), "--out", str(out)])
            original = out.read_bytes()
            self.assertIn("ours -> peer", console.getvalue())
            self.assertIn("peer/seller", console.getvalue())
            with self.assertRaises(FileExistsError):
                summaries.main([str(source), "--out", str(out)])
            self.assertEqual(out.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
