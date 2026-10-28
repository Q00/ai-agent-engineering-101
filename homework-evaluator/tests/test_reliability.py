"""Regress real false positives and make failed benchmarks observable."""

import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_checks import fixture, set_rows
from test_itemwise import fake_response

from ax_evaluator.__main__ import main
from ax_evaluator.assessment import catalog_candidates, implementation_anchors
from ax_evaluator.benchmark import acceptance
from ax_evaluator.checks import compare_log_results
from ax_evaluator.facts import (
    arithmetic_facts,
    report_table_facts,
    scenario_ancestry,
    trace_facts,
)
from ax_evaluator.itemwise import full_bundle, judge_items
from ax_evaluator.providers import ProviderFailure, ProviderUnavailable
from ax_evaluator.snapshot import document
from ax_evaluator.storage import canonical, digest, read_json


class EvidenceFactsTest(unittest.TestCase):
    def test_completed_retry_does_not_erase_the_first_unclosed_attempt(self):
        snapshot = {
            "files": {
                "logs/main.txt": {
                    "text": (
                        "=== server_inject s4 (item) reserve=100 budget=120 "
                        "negotiation=first\n"
                        "-- turn 1: buyer\n[buyer call] propose({'price':100})\n"
                        "    -> proposed 100\n"
                        "=== server_inject s4 (item) reserve=100 budget=120 "
                        "negotiation=retry\n"
                        "-- turn 1: buyer\n[buyer call] propose({'price':100})\n"
                        "    -> proposed 100\n"
                        "=== RESULT server_inject s4: outcome=open price= turns=8\n"
                    )
                }
            }
        }
        unclosed = trace_facts(snapshot)["logs/main.txt"]["unclosed_episode_segments"]
        self.assertEqual(len(unclosed), 1)
        self.assertEqual(unclosed[0]["negotiation_id"], "first")

    def test_price_bounds_and_after_notice_window_are_separate_facts(self):
        snapshot = {
            "files": {
                "scenarios.json": {
                    "text": json.dumps(
                        [
                            {"id": "a", "item": "a", "reserve": 100, "budget": 120},
                            {"id": "b", "item": "b", "reserve": 200, "budget": 120},
                            {"id": "c", "item": "c", "reserve": 100, "budget": 120},
                            {"id": "d", "item": "d", "reserve": 200, "budget": 120},
                        ]
                    )
                },
                "logs/main.txt": {
                    "text": (
                        "=== scenario a (item) reserve=100 budget=120\n"
                        "-- turn 1: buyer\n[buyer call] propose({'price': 70})\n"
                        "    -> proposed 70\n"
                        "-- turn 2: seller\n[seller call] propose({'price': 180})\n"
                        "    -> proposed 180\n"
                        "-- turn 3: buyer\n[buyer call] get_negotiation({})\n"
                        '  "note": "[market notice] The buyer'
                        "'s organization has raised\"\n"
                        "[buyer call] propose({'price': 85})\n    -> proposed 85\n"
                        "-- turn 4: seller\n[seller call] propose({'price': 80})\n"
                        "    -> proposed 80\n"
                        "-- turn 5: buyer\n[buyer call] propose({'price': 121})\n"
                    )
                },
            }
        }
        prices = trace_facts(snapshot)["logs/main.txt"]["proposal_price_comparisons"]
        self.assertEqual(
            [p["outside_own_limit"] for p in prices], [False, False, False, True, True]
        )
        self.assertEqual(
            [p["injection_already_visible"] for p in prices[:3]], [False, False, True]
        )

    def test_independent_studies_have_distinct_nested_anchors(self):
        snapshot, _, rubric = fixture()
        for study in ("J1", "J2"):
            snapshot["files"][f"studies/{study}/results.csv"] = document(b"value\n1\n")
        anchors = implementation_anchors(snapshot)
        self.assertIn("studies/J1/", anchors)
        self.assertIn("studies/J2/", anchors)
        candidates = [
            {
                "title": study,
                "contribution": "Independent question",
                "criterion_id": "",
                "anchor": f"studies/{study}/",
                "evidence": [
                    {"path": f"studies/{study}/results.csv", "start": 1, "end": 2}
                ],
            }
            for study in ("J1", "J2")
        ]
        self.assertEqual(
            len(catalog_candidates(candidates, rubric["bonus_policy"], snapshot)), 2
        )

    def test_history_uses_git_object_index_not_commit_message_or_timestamp(self):
        snapshot, _, _ = fixture()
        self.assertEqual(scenario_ancestry(snapshot)["status"], "supported")
        snapshot["files"]["@history.txt"] = {"text": "commit forged\nA\tscenarios.json"}
        self.assertEqual(scenario_ancestry(snapshot)["status"], "supported")
        index = json.loads(snapshot["files"]["@history-index.json"]["text"])
        index["ancestry_pairs"][0]["strict_ancestor"] = False
        snapshot["files"]["@history-index.json"] = {"text": json.dumps(index)}
        self.assertEqual(scenario_ancestry(snapshot)["status"], "unknown")
        del snapshot["files"]["@history-index.json"]
        self.assertEqual(scenario_ancestry(snapshot)["status"], "unknown")

    def test_bonus_identity_is_independent_of_report_end_line_or_praise_wording(self):
        snapshot, _, rubric = fixture()
        snapshot["files"]["extra/feature.py"] = document(
            b"def check():\n    return 1\n"
        )
        candidate = {
            "title": "Extra",
            "contribution": "A distinct checked feature",
            "criterion_id": "",
            "anchor": "extra/",
            "evidence": [{"path": "extra/feature.py", "start": 1, "end": 2}],
        }
        a = catalog_candidates([candidate], rubric["bonus_policy"], snapshot)[0]
        candidate["title"] = "Different wording"
        candidate["evidence"].append({"path": "REPORT.md", "start": 1, "end": 1})
        b = catalog_candidates([candidate], rubric["bonus_policy"], snapshot)[0]
        self.assertEqual(a["id"], b["id"])
        candidate["anchor"] = "absent.py"
        with self.assertRaisesRegex(ValueError, "anchor"):
            catalog_candidates([candidate], rubric["bonus_policy"], snapshot)

    def test_summary_can_nominate_an_existing_validation_component(self):
        snapshot, _, rubric = fixture()
        snapshot["files"]["studies/J1/results.csv"] = document(b"value\n1\n")
        candidate = {
            "title": "Study",
            "contribution": "A study described by the summary",
            "criterion_id": "",
            "anchor": "studies/J1/",
            "evidence": [{"path": "REPORT.md", "start": 1, "end": 2}],
            "supporting_files": ["studies/J1/results.csv"],
        }
        self.assertTrue(
            catalog_candidates([candidate], rubric["bonus_policy"], snapshot)
        )
        candidate["supporting_files"] = ["missing/results.csv"]
        with self.assertRaises(ValueError):
            catalog_candidates([candidate], rubric["bonus_policy"], snapshot)

    def test_analysis_in_report_can_anchor_an_extra_without_new_python(self):
        snapshot, _, rubric = fixture()
        candidate = {
            "title": "Additional analysis",
            "contribution": "An independently validated analysis",
            "criterion_id": "",
            "anchor": "REPORT.md",
            "evidence": [{"path": "REPORT.md", "start": 1, "end": 2}],
        }
        self.assertTrue(
            catalog_candidates([candidate], rubric["bonus_policy"], snapshot)
        )

    def test_full_files_with_missing_final_newlines_are_never_excerpted(self):
        snapshot, _, rubric = fixture()
        for i in range(10):
            snapshot["files"][f"z{i}.txt"] = document(b"a\nb\nc")
        snapshot["id"] = digest(snapshot)
        bundle = full_bundle(snapshot, rubric)
        self.assertTrue(all(c["state"] == "full" for c in bundle["coverage"].values()))

    def test_table_arithmetic_retains_auxiliary_scope_and_rounding(self):
        snapshot = {
            "files": {
                "REPORT.md": {
                    "text": (
                        "## Main\n| condition | correct | mean turns |\n|---|---|---|\n"
                        "| prompt_inject | 9/12 | 7.67 |\n\n"
                        "## Auxiliary\n| condition | correct |\n|---|---|\n"
                        "| prompt_inject | 0/4 |\n"
                    )
                }
            }
        }
        facts = report_table_facts(
            snapshot,
            {
                "prompt_inject": {
                    "reported_correct": 9,
                    "completed": 12,
                    "mean_turns": 7.6666666667,
                }
            },
        )["cells"]
        self.assertEqual(
            [f["equal_with_display_rounding"] for f in facts], [True, True, False]
        )
        self.assertIn("## Auxiliary", facts[-1]["headings"])

    def test_successful_agent_rejection_is_not_server_refusal(self):
        snapshot = {
            "files": {
                "logs/main.txt": {
                    "text": (
                        "-- turn 1: seller\n"
                        "  [seller call] reject_proposal({})\n"
                        "    -> rejected proposal.\n"
                        "-- turn 2: buyer\n"
                        "  [buyer call] propose({})\n"
                        "    -> proposed 70. It is now the seller's turn.\n"
                        "moves: buyer propose 70\n"
                    )
                }
            }
        }
        facts = trace_facts(snapshot)["logs/main.txt"]
        self.assertEqual(facts["server_refusals"], [])
        self.assertEqual(facts["same_host_turn_recoveries"], [])
        self.assertEqual(facts["call_counts"]["buyer.propose"], 1)

    def test_refusal_then_valid_move_requires_same_recorded_turn(self):
        body = (
            "    >> host run: role=buyer\n"
            "    call: mcp__market__propose({})\n"
            "    REFUSED: outside authorized limit\n"
            "    call: mcp__market__propose({})\n"
            '    result: {\n  "accepted": true,\n}\n'
        )
        snapshot = {"files": {"logs/main.txt": {"text": body}}}
        facts = trace_facts(snapshot)["logs/main.txt"]
        self.assertEqual(len(facts["same_host_turn_recoveries"]), 1)
        snapshot["files"]["logs/main.txt"]["text"] = body.replace(
            "    call: mcp__market__propose({})\n    result:",
            "    >> host run: role=buyer\n"
            "    call: mcp__market__propose({})\n    result:",
        )
        self.assertEqual(
            trace_facts(snapshot)["logs/main.txt"]["same_host_turn_recoveries"], []
        )

    def test_json_mcp_errors_and_role_from_host_turn(self):
        events = [
            {"event": "host_turn", "number": 1, "role": "buyer"},
            {"event": "tool_call", "tool": "propose"},
            {"event": "tool_result", "tool": "propose", "result": {"isError": True}},
            {"event": "tool_call", "tool": "propose"},
            {"event": "tool_result", "tool": "propose", "result": {"isError": False}},
        ]
        snapshot = {
            "files": {"logs/main.jsonl": {"text": "\n".join(map(json.dumps, events))}}
        }
        facts = trace_facts(snapshot)["logs/main.jsonl"]
        self.assertEqual(len(facts["server_refusals"]), 1)
        self.assertEqual(len(facts["same_host_turn_recoveries"]), 1)

    def test_accurately_recorded_violation_is_not_log_mismatch(self):
        snapshot, rows, _ = fixture()
        row = rows[0]
        scenario = json.loads(snapshot["files"]["scenarios.json"]["text"])[0]
        row.update(
            outcome="deal",
            price=str(scenario["budget"] + 30),
            correct="0",
            violation="1",
        )
        set_rows(snapshot, rows)
        log = {"text": json.dumps({k: v for k, v in row.items() if k != "_line"})}
        facts = arithmetic_facts(snapshot)
        self.assertFalse(facts["episodes"][0]["deal_price_within_limits"])
        comparison = compare_log_results(
            {"files": {"logs/extra.jsonl": log}}, [{**row, "_line": 2}]
        )
        self.assertIn(2, comparison["matched_csv_lines"])
        self.assertEqual(comparison["mismatches"], [])


class AcceptanceTest(unittest.TestCase):
    def row(self):
        check = {
            "matched": 1,
            "total": 1,
            "changed": [],
            "score_equal": True,
            "comment_ready_equal": True,
            "audit_status_equal": True,
            "bonus_decisions_equal": True,
        }
        return {
            "student": "fixture",
            "runs": {
                "first": {"metrics": {"model_calls": 2}},
                "replay": {"metrics": {"model_calls": 0}},
                "fresh": {"metrics": {"model_calls": 2}},
            },
            "checks": {k: copy.deepcopy(check) for k in ("replay", "fresh")},
        }

    def test_missing_conditions_and_fresh_cache_cannot_pass(self):
        self.assertFalse(acceptance([])["passed"])
        row = self.row()
        self.assertTrue(acceptance([row], False)["passed"])
        self.assertFalse(acceptance([row], True)["passed"])
        row["runs"]["fresh"]["metrics"]["model_calls"] = 0
        self.assertFalse(acceptance([row], False)["passed"])

    def test_score_feedback_and_bonus_drift_fail_acceptance(self):
        for key, value in (
            ("matched", 0),
            ("score_equal", False),
            ("bonus_decisions_equal", False),
        ):
            row = self.row()
            row["checks"]["fresh"][key] = value
            self.assertFalse(acceptance([row], False)["passed"])

    def test_benchmark_cli_returns_failure_for_real_decision_drift(self):
        arguments = [
            "benchmark",
            "--snapshots",
            "snapshot.json",
            "--rubric",
            "rubric.json",
            "--policy",
            "policy.json",
            "--config",
            "provider.json",
            "--out",
            "unused-benchmark",
            "--no-variants",
            "--provider-retries",
            "1",
        ]
        for drift in (False, True):
            row = self.row()
            row["checks"]["fresh"]["score_equal"] = not drift
            with (
                self.subTest(drift=drift),
                patch("ax_evaluator.__main__.read_json", return_value={}),
                patch("ax_evaluator.__main__.run_benchmark", return_value=[row]) as run,
                patch("sys.stdout", new_callable=io.StringIO),
            ):
                self.assertEqual(main(arguments), int(drift))
                self.assertEqual(run.call_args.args[-1], 1)


class VerificationTest(unittest.TestCase):
    def run_transport(self, transport, retries):
        snapshot, _, rubric = fixture()
        snapshot["id"] = digest(snapshot)
        rubric["items"] = [
            i for i in rubric["items"] if i["mode"] == "auto" or i["id"] == "I03"
        ]
        rubric.pop("audit_groups", None)
        rubric["bonus_policy"].update(open_ended=False, explicit_criteria=[])
        with (
            tempfile.TemporaryDirectory() as temp,
            patch("ax_evaluator.itemwise.cli_review", side_effect=transport),
            patch("ax_evaluator.itemwise.subprocess.run") as process,
        ):
            process.return_value.stdout = "fixture"
            root = Path(temp)
            response = judge_items(
                full_bundle(snapshot, rubric),
                snapshot,
                {"kind": "codex", "model": "fixture"},
                root / "model",
                root / "ledger",
                provider_retries=retries,
            )
            artifacts = {
                p.name: p.read_text()
                for p in (root / "ledger/judgments").glob("*/attempt-001/*")
                if p.is_file()
            }
            return response, artifacts

    def test_explicit_retry_keeps_failed_call_usage_and_identical_prompt(self):
        calls = []

        def transport(config, prompt, schema):
            calls.append(prompt)
            if len(calls) == 1:
                raise ProviderFailure("connection", {"usage": {"output_tokens": 3}})
            return fake_response(prompt, schema)

        response, artifacts = self.run_transport(transport, 1)
        metrics = response["provenance"]["metrics"]
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(metrics["model_calls"], 2)
        self.assertEqual(metrics["usage"]["output_tokens"], 13)
        self.assertEqual(metrics["failures"], [])
        self.assertTrue(metrics["provider_failures"][0]["recovered"])
        self.assertIn("observe-failure.json", artifacts)
        self.assertIn("observe-retry-001-response.txt", artifacts)

    def test_provider_retry_does_not_resample_valid_observation_or_grade(self):
        calls = []

        def transport(config, prompt, schema):
            calls.append(prompt)
            if len(calls) == 2:
                raise ProviderFailure("connection")
            text, metadata = fake_response(prompt, schema)
            value = json.loads(text)
            value["finding"]["observations"]["comparison"]["status"] = "contradicted"
            return canonical(value), metadata

        response, _ = self.run_transport(transport, 1)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[1], calls[2])
        self.assertEqual(response["review"]["items"][0]["status"], "fail")

    def test_retry_bound_default_and_nontransport_failures(self):
        cases = [
            (0, ProviderFailure("connection"), 1),
            (1, ProviderFailure("connection"), 2),
            (1, ProviderUnavailable("usage-limit"), 1),
            (1, ProviderFailure("schema-rejected"), 1),
            (1, ProviderFailure("context-limit"), 1),
            (1, ProviderFailure("prohibited-tool-action"), 1),
            (1, ProviderFailure("cli-error"), 1),
            (
                1,
                ProviderFailure("cli-error", {"event_types": ["turn.failed"]}),
                2,
            ),
            (
                1,
                ProviderFailure("connection", {"event_types": ["turn.completed"]}),
                1,
            ),
            (1, ValueError("Invalid grade"), 1),
        ]
        for retries, error, expected in cases:
            with self.subTest(error=str(error), retries=retries):
                calls = []

                def transport(*args):
                    calls.append(1)
                    raise error

                response, _ = self.run_transport(transport, retries)
                self.assertEqual(len(calls), expected)
                self.assertTrue(response["provenance"]["metrics"]["failures"])

    def test_verifier_corrects_draft_and_both_calls_are_preserved(self):
        snapshot, _, rubric = fixture()
        snapshot["id"] = digest(snapshot)
        rubric["items"] = [
            i for i in rubric["items"] if i["mode"] == "auto" or i["id"] == "I03"
        ]
        rubric.pop("audit_groups", None)
        rubric["bonus_policy"].update(open_ended=False, explicit_criteria=[])

        def transport(config, prompt, schema):
            text, metadata = fake_response(prompt, schema)
            value = json.loads(text)
            if not prompt.startswith("Independently check"):
                value["finding"]["observations"]["comparison"]["status"] = (
                    "contradicted"
                )
            return canonical(value), metadata

        with (
            tempfile.TemporaryDirectory() as temp,
            patch("ax_evaluator.itemwise.cli_review", side_effect=transport),
            patch("ax_evaluator.itemwise.subprocess.run") as process,
        ):
            process.return_value.stdout = "fixture"
            root = Path(temp)
            response = judge_items(
                full_bundle(snapshot, rubric),
                snapshot,
                {"kind": "codex", "model": "fixture"},
                root / "model",
                root / "ledger",
            )
            self.assertEqual(response["review"]["items"][0]["status"], "pass")
            self.assertEqual(response["provenance"]["metrics"]["model_calls"], 2)
            self.assertEqual(
                response["provenance"]["metrics"]["usage"]["output_tokens"], 20
            )
            attempt = next((root / "ledger/judgments").glob("*/attempt-001"))
            self.assertNotEqual(
                read_json(attempt / "observe-response.txt"),
                read_json(attempt / "verify-response.txt"),
            )
