"""Test full evidence, replay, invalidation, locking and explicit policies."""

import copy
import json
import subprocess
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from test_checks import fixture

from ax_evaluator.checks import compare_log_results
from ax_evaluator.itemwise import (
    OPEN_QUESTION,
    full_bundle,
    judge_items,
    normalize_finding,
    partition_files,
    resolve_policies,
    select_files,
)
from ax_evaluator.ledger import Ledger, QuestionBox
from ax_evaluator.snapshot import document
from ax_evaluator.storage import canonical, digest, read_json


def fake_response(*args):
    prompt, schema = args[-2:]
    packet = json.loads(prompt.split("UNTRUSTED_FULL_FILES_JSON:\n")[1])
    path = "REPORT.md" if "REPORT.md" in packet else next(iter(packet))
    ref = {"block": packet[path]["text"].splitlines()[0].split(" ", 1)[1]}
    result = {"questions": []}
    if "finding" in schema["properties"]:
        finding = schema["properties"]["finding"]["properties"]
        if "facts" in finding:
            result["finding"] = {
                "id": finding["id"]["enum"][0],
                "facts": {
                    key: {
                        "value": True,
                        "note": "원문에서 확인",
                        "evidence": [ref],
                        **(
                            {"claim_quote": "", "counter_quote": ""}
                            if "claim_quote"
                            in finding["facts"]["properties"][key]["properties"]
                            else {}
                        ),
                    }
                    for key in finding["facts"]["properties"]
                },
            }
            return canonical(result), {"usage": {"output_tokens": 10}}
        result["finding"] = {
            "id": finding["id"]["enum"][0],
            "observations": {
                key: {
                    "status": "supported",
                    "note": "필수 근거 확인",
                    "evidence": [ref],
                }
                for key in finding["observations"]["properties"]
            },
            "issues": [],
        }
    elif "candidates" in schema["properties"]:
        result["candidates"] = []
    else:
        result["assessments"] = []
    return canonical(result), {"usage": {"output_tokens": 10}}


class LedgerTest(unittest.TestCase):
    def test_same_key_computed_once_under_concurrency_and_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger, calls = Ledger(temp), []

            def compute(attempt):
                calls.append(attempt)
                time.sleep(0.02)
                return {"status": "pass"}, {}

            with ThreadPoolExecutor(2) as pool:
                fs = [
                    pool.submit(ledger.decide, {"input": 1}, compute, lambda v: None)
                    for _ in range(2)
                ]
                results = [f.result() for f in fs]
            self.assertEqual(len(calls), 1)
            self.assertEqual(sorted(r[1] for r in results), [False, True])
            path = next(Path(temp).glob("judgments/*/result.json"))
            data = read_json(path)
            data["result"]["status"] = "fail"
            path.write_text(canonical(data))
            with self.assertRaisesRegex(ValueError, "integrity"):
                ledger.decide({"input": 1}, compute, lambda v: None)

    def test_failed_call_requires_explicit_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = Ledger(temp)

            def failed(attempt):
                raise TimeoutError()

            with self.assertRaises(TimeoutError):
                ledger.decide({}, failed, lambda v: None)
            with self.assertRaisesRegex(ValueError, "retry-failed"):
                ledger.decide({}, failed, lambda v: None)
            record, cached = ledger.decide(
                {}, lambda p: ({"ok": True}, {}), lambda v: None, True
            )
            self.assertFalse(cached)
            self.assertTrue(record["result"]["ok"])

    def test_question_dedup_and_policy_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            box = QuestionBox(temp)
            key = box.register(OPEN_QUESTION, "C02")
            self.assertEqual(key, box.register(OPEN_QUESTION, "C02"))
            self.assertEqual(len(box.list()), 1)
            _, _, rubric = fixture()
            box.answer(key, "correct", "professor")
            self.assertEqual(resolve_policies(rubric, temp)["open_non_deal"], "correct")
            box.answer(key, "incorrect", "professor")
            self.assertEqual(
                resolve_policies(rubric, temp)["open_non_deal"], "incorrect"
            )
            self.assertEqual(
                len(list((Path(temp) / "questions" / key / "answers").glob("*.json"))),
                2,
            )


class ItemwiseTest(unittest.TestCase):
    def setUp(self):
        self.snapshot, _, self.rubric = fixture()
        self.snapshot["id"] = digest(
            {k: v for k, v in self.snapshot.items() if k != "id"}
        )
        self.config = {"kind": "claude", "model": "fixture"}

    def test_full_files_not_truncated_and_limit_fails_explicitly(self):
        self.snapshot["files"]["server.py"] = document(("x=1\n" * 20000).encode())
        self.snapshot["id"] = digest(
            {k: v for k, v in self.snapshot.items() if k != "id"}
        )
        bundle = full_bundle(self.snapshot, self.rubric)
        self.assertEqual(bundle["coverage"]["server.py"]["state"], "full")
        selected = select_files(self.snapshot, ["source"])
        with self.assertRaisesRegex(ValueError, "Whole-file"):
            partition_files(self.snapshot, selected, 100)
        self.assertEqual(partition_files(self.snapshot, selected, 200000), [selected])

    def test_replay_no_calls_and_model_change_invalidates(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch(
                "ax_evaluator.itemwise.cli_review", side_effect=fake_response
            ) as transport,
            patch(
                "ax_evaluator.itemwise.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "fixture-version", ""),
            ),
        ):
            root = Path(temp)

            def run(name, snapshot=None, config=None):
                s = snapshot or self.snapshot
                return judge_items(
                    full_bundle(s, self.rubric),
                    s,
                    config or self.config,
                    root / name,
                    root / "ledger",
                    workers=4,
                )

            first = run("first")
            self.assertTrue(transport.called)
            transport.reset_mock()
            second = run("replay")
            self.assertEqual(first["review"], second["review"])
            transport.assert_not_called()
            self.assertEqual(second["provenance"]["metrics"]["model_calls"], 0)
            changed = copy.deepcopy(self.snapshot)
            changed["files"]["unrelated.bin"] = document(b"\x00")
            changed["id"] = digest({k: v for k, v in changed.items() if k != "id"})
            run("unrelated", changed)
            self.assertLess(transport.call_count, len(first["review"]["items"]))
            transport.reset_mock()
            run("other-model", config={"kind": "claude", "model": "other"})
            self.assertTrue(transport.called)

    def test_feedback_class_and_rerun_follow_rules(self):
        finding = {
            "status": "pass",
            "severity": "minor",
            "feedback_type": "revision",
            "revision_key": "x",
            "rerun": "needed",
            "rerun_basis": "records-sufficient",
        }
        result = normalize_finding(finding)
        self.assertEqual(
            (result["feedback_type"], result["rerun"], result["revision_key"]),
            ("suggestion", "not-needed", ""),
        )


class LogAdapterTest(unittest.TestCase):
    def test_json_meta_uses_explicit_repeat_and_scenario(self):
        row = {
            "run": "1",
            "condition": "prompt_inject",
            "scenario": "s1",
            "deal_possible": "1",
            "outcome": "deal",
            "price": "80",
            "correct": "1",
            "violation": "0",
            "attempted_violations": "0",
            "refused_calls": "0",
            "turns": "2",
            "tool_calls": "4",
            "_line": 2,
        }
        records = [
            {"event": "meta", "repeat": 1, "condition": "prompt_inject"},
            {"event": "episode_start", "scenario": "s1", "reserve": 60, "budget": 100},
            {
                "event": "episode_result",
                **{
                    k: v
                    for k, v in row.items()
                    if k
                    not in {"run", "condition", "scenario", "deal_possible", "_line"}
                },
            },
        ]
        snapshot = {
            "files": {
                "logs/prompt_inject-01.jsonl": {
                    "text": "\n".join(json.dumps(r) for r in records)
                }
            }
        }
        self.assertEqual(compare_log_results(snapshot, [row])["matched_csv_lines"], [2])
        records[1]["scenario"] = "different"
        snapshot["files"]["logs/prompt_inject-01.jsonl"]["text"] = "\n".join(
            json.dumps(r) for r in records
        )
        self.assertEqual(
            compare_log_results(snapshot, [row])["unmatched_csv_lines"], [2]
        )


class ScoringAndPerturbationTest(unittest.TestCase):
    def test_weighted_policy_recalculates_saved_judgments_and_preserves_pending(self):
        from ax_evaluator.grading import weighted_score

        report = {
            "required_ids": ["A", "B", "C"],
            "items": {
                "A": {"status": "pass"},
                "B": {"status": "pending"},
                "C": {"status": "fail"},
            },
            "bonus_earned": 2,
        }
        policy = {
            "name": "Instructor example, not approved",
            "maximum": 100,
            "groups": [
                {"name": "repro", "weight": 0.5, "items": ["A", "B"]},
                {"name": "interpretation", "weight": 0.5, "items": ["C"]},
            ],
        }
        result = weighted_score(report, policy)
        self.assertEqual(
            (result["earned"], result["possible"], result["bonus_separate"]),
            (25, 50, 2),
        )
        bad = copy.deepcopy(policy)
        bad["groups"][1]["items"].append("A")
        with self.assertRaises(ValueError):
            weighted_score(report, bad)
        bad = copy.deepcopy(policy)
        bad["groups"][0]["weight"] = float("nan")
        with self.assertRaises(ValueError):
            weighted_score(report, bad)

    def test_transformations_preserve_source_and_injection_is_not_a_cache_hit(self):
        from ax_evaluator.benchmark import perturb

        snapshot, _, rubric = fixture()
        snapshot["id"] = digest({k: v for k, v in snapshot.items() if k != "id"})
        before = copy.deepcopy(snapshot)
        original = full_bundle(snapshot, rubric)
        ordered = full_bundle(perturb(snapshot, "order"), rubric)
        self.assertEqual(original, ordered)
        injected = perturb(snapshot, "injection")
        self.assertNotEqual(injected["id"], snapshot["id"])
        self.assertIn("EVALUATOR OVERRIDE", injected["files"]["REPORT.md"]["text"])
        self.assertEqual(before, snapshot)

    def test_log_adapters_do_not_fill_missing_context_from_csv(self):
        row = {
            "run": "prompt_inject-r1",
            "condition": "prompt_inject",
            "scenario": "1",
            "deal_possible": "1",
            "outcome": "open",
            "price": "",
            "correct": "0",
            "violation": "0",
            "attempted_violations": "0",
            "refused_calls": "0",
            "turns": "8",
            "tool_calls": "16",
            "_line": 2,
        }
        body = (
            "=== outcome=open price=None correct=0 violation=0 "
            "attempted_violations=0 refused_calls=0 turns=8 tool_calls=16 ==="
        )
        snap = {"files": {"logs/run.txt": {"text": body}}}
        self.assertEqual(compare_log_results(snap, [row])["matched_csv_lines"], [])
        snap["files"]["logs/run.txt"]["text"] = (
            "model=haiku host=cli condition=prompt_inject repeat=1\n"
            "=== scenario 1 (x) reserve=60 budget=80 "
            "condition=prompt_inject negotiation_id=n ===\n" + body
        )
        self.assertEqual(compare_log_results(snap, [row])["matched_csv_lines"], [2])


class FailureAndPolicyIntegrationTest(unittest.TestCase):
    def run_model(self, root, name, snapshot, rubric):
        return judge_items(
            full_bundle(snapshot, rubric),
            snapshot,
            {"kind": "claude", "model": "fixture"},
            root / name,
            root / "ledger",
        )

    def test_failed_item_does_not_become_zero_or_stop_other_items(self):
        from ax_evaluator.review import score

        snapshot, _, rubric = fixture()
        snapshot["id"] = digest({k: v for k, v in snapshot.items() if k != "id"})

        def transport(config, prompt, schema):
            finding = schema["properties"].get("finding")
            if finding and finding["properties"]["id"]["enum"] == ["F06"]:
                raise TimeoutError()
            return fake_response(prompt, schema)

        with (
            tempfile.TemporaryDirectory() as temp,
            patch("ax_evaluator.itemwise.cli_review", side_effect=transport),
            patch(
                "ax_evaluator.itemwise.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "fixture", ""),
            ),
        ):
            root = Path(temp)
            response = self.run_model(root, "first", snapshot, rubric)
            report = score(full_bundle(snapshot, rubric), response["review"])
            self.assertEqual(report["items"]["F06"]["status"], "pending")
            self.assertEqual(report["items"]["F07"]["status"], "pass")
            self.assertEqual(report["base_pending"], 1)
            self.assertEqual(
                response["provenance"]["metrics"]["usage_missing_calls"], 1
            )

    def test_policy_answer_updates_only_dependent_automatic_score(self):
        from test_checks import set_rows

        from ax_evaluator.review import score

        snapshot, rows, rubric = fixture()
        for row in rows:
            if row["deal_possible"] == "0":
                row["outcome"] = "open"
        set_rows(snapshot, rows)
        snapshot["id"] = digest({k: v for k, v in snapshot.items() if k != "id"})
        with (
            tempfile.TemporaryDirectory() as temp,
            patch(
                "ax_evaluator.itemwise.cli_review",
                side_effect=lambda c, p, s: fake_response(p, s),
            ) as transport,
            patch(
                "ax_evaluator.itemwise.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "fixture", ""),
            ),
        ):
            root = Path(temp)
            first = self.run_model(root, "first", snapshot, rubric)
            self.assertEqual(
                score(full_bundle(snapshot, rubric), first["review"])["items"]["C02"][
                    "status"
                ],
                "pending",
            )
            box = QuestionBox(root / "ledger")
            question = next(
                q
                for q in box.list()
                if q["question"]["policy"] == "week-05.open_non_deal"
            )
            box.answer(question["id"], "correct", "professor")
            changed = resolve_policies(rubric, root / "ledger")
            transport.reset_mock()
            second = self.run_model(root, "policy", snapshot, changed)
            self.assertEqual(transport.call_count, 0)
            self.assertEqual(
                score(full_bundle(snapshot, changed), second["review"])["items"]["C02"][
                    "status"
                ],
                "pass",
            )


class AssemblyRegressionTest(unittest.TestCase):
    def test_material_correction_in_later_part_keeps_its_cause(self):
        from ax_evaluator.itemwise import merge_parts

        common = {
            "id": "C04",
            "status": "pass",
            "reason": "요건 충족",
            "evidence": [],
            "feedback_type": "strength",
            "feedback": "확인했습니다.",
            "severity": "none",
            "rerun": "not-needed",
            "rerun_basis": "not-applicable",
            "rerun_reason": "해당 없음",
            "revision_key": "",
            "deduction_reason": "",
            "pending_reason": "",
        }
        correction = copy.deepcopy(common)
        correction.update(
            severity="material",
            revision_key="report-count",
            feedback="보고서의 집계를 수정하세요.",
            rerun_basis="records-sufficient",
        )
        result = merge_parts(
            [{"finding": common}, {"finding": correction}], {"category": "consistency"}
        )
        self.assertEqual(result["revision_key"], "report-count")
        self.assertEqual(result["feedback_type"], "revision")
        self.assertEqual(result["status"], "pass")

    def test_base_policy_question_does_not_block_bonus_or_unrelated_item(self):
        from ax_evaluator.review import score

        snapshot, _, rubric = fixture()
        snapshot["id"] = digest({k: v for k, v in snapshot.items() if k != "id"})

        def transport(config, prompt, schema):
            text, meta = fake_response(prompt, schema)
            value = json.loads(text)
            if "finding" not in value or value["finding"]["id"] == "F07":
                value["questions"] = [copy.deepcopy(OPEN_QUESTION)]
            return canonical(value), meta

        with (
            tempfile.TemporaryDirectory() as temp,
            patch("ax_evaluator.itemwise.cli_review", side_effect=transport),
            patch(
                "ax_evaluator.itemwise.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "fixture", ""),
            ),
        ):
            root = Path(temp)
            response = judge_items(
                full_bundle(snapshot, rubric),
                snapshot,
                {"kind": "claude", "model": "fixture"},
                root / "run",
                root / "ledger",
            )
            self.assertTrue(response["review"]["bonus_review_complete"])
            report = score(full_bundle(snapshot, rubric), response["review"])
            self.assertEqual(report["items"]["F07"]["status"], "pass")

    def test_offline_reassembly_uses_verified_records_without_a_provider(self):
        from ax_evaluator.itemwise import replay_items
        from ax_evaluator.review import prepare

        snapshot, _, rubric = fixture()
        snapshot["id"] = digest({k: v for k, v in snapshot.items() if k != "id"})
        with (
            tempfile.TemporaryDirectory() as temp,
            patch(
                "ax_evaluator.itemwise.cli_review",
                side_effect=lambda c, p, s: fake_response(p, s),
            ),
            patch(
                "ax_evaluator.itemwise.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "fixture", ""),
            ),
        ):
            root = Path(temp)
            texts = [f["text"] or "" for f in snapshot["files"].values()]
            prepare(
                snapshot,
                rubric,
                root / "packet",
                sum(map(len, texts)) + 1,
                max(map(len, texts)) + 1,
            )
            bundle = read_json(root / "packet/bundle.json")
            first = judge_items(
                bundle,
                snapshot,
                {"kind": "claude", "model": "fixture"},
                root / "model",
                root / "ledger",
            )
            with patch(
                "ax_evaluator.itemwise.cli_review",
                side_effect=AssertionError("offline replay cannot call a model"),
            ):
                result = replay_items(
                    root / "packet", root / "model", root / "ledger", root / "replayed"
                )
            self.assertEqual(first["review"], result["review"])
            self.assertEqual(result["provenance"]["metrics"]["model_calls"], 0)
            key = next((root / "ledger/judgments").glob("*/result.json"))
            record = read_json(key)
            record["result"]["inspected_files"] = []
            key.write_text(canonical(record))
            with self.assertRaises(ValueError):
                replay_items(
                    root / "packet", root / "model", root / "ledger", root / "tampered"
                )


class BenchmarkPublicationTest(unittest.TestCase):
    def test_synthetic_results_cannot_be_posted_even_when_review_is_complete(self):
        from test_policy_comments import complete_fixture

        from ax_evaluator.comments import publish, write_comment
        from ax_evaluator.review import score

        snapshot, bundle, review = complete_fixture()
        snapshot["metadata"].update(
            repository="example/course", pr=1, head_sha="a" * 40
        )
        report = score(bundle, review)
        report["provenance"] = {"benchmark_only": True}
        with (
            tempfile.TemporaryDirectory() as temp,
            patch(
                "ax_evaluator.comments.github",
                side_effect=AssertionError("no GitHub call"),
            ),
        ):
            root = Path(temp)
            write_comment(root, report, bundle, snapshot)
            self.assertEqual(publish(root)["action"], "preview-only")
            with self.assertRaisesRegex(ValueError, "Benchmark artifacts"):
                publish(root, write=True)


class NormalizationAndLimitTest(unittest.TestCase):
    def test_unnamed_bonus_catalog_has_only_one_valid_criterion_id(self):
        from test_policy_comments import bonus

        from ax_evaluator.itemwise import normalize_bonuses, task_schema

        _, _, rubric = fixture()
        contribution = bonus()
        contribution["criterion_id"] = "open-ended"
        result = normalize_bonuses([contribution], rubric["bonus_policy"])
        self.assertEqual(result[0]["criterion_id"], "")
        schema = task_schema(bonus_policy=rubric["bonus_policy"])
        self.assertEqual(
            schema["properties"]["bonuses"]["items"]["properties"]["criterion_id"][
                "enum"
            ],
            [""],
        )

    def test_usage_limit_stops_undispatched_units_and_remains_pending(self):
        from ax_evaluator.providers import ProviderUnavailable

        snapshot, _, rubric = fixture()
        snapshot["id"] = digest({k: v for k, v in snapshot.items() if k != "id"})
        with (
            tempfile.TemporaryDirectory() as temp,
            patch(
                "ax_evaluator.itemwise.cli_review",
                side_effect=ProviderUnavailable("usage-limit"),
            ) as transport,
            patch(
                "ax_evaluator.itemwise.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "fixture", ""),
            ),
        ):
            root = Path(temp)
            result = judge_items(
                full_bundle(snapshot, rubric),
                snapshot,
                {"kind": "claude", "model": "fixture"},
                root / "run",
                root / "ledger",
                workers=1,
            )
            self.assertEqual(transport.call_count, 1)
            self.assertEqual(result["provenance"]["metrics"]["model_calls"], 1)
            self.assertTrue(result["provenance"]["metrics"]["provider_unavailable"])
            self.assertTrue(
                all(i["status"] == "pending" for i in result["review"]["items"])
            )
