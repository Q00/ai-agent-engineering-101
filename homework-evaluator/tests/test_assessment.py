"""Regression gates for grading boundaries, open bonuses and policy propagation."""

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_checks import fixture, set_rows
from test_itemwise import fake_response

from ax_evaluator.assessment import (
    BONUS_CHECKS,
    assemble_audits,
    catalog_candidates,
    compile_bonuses,
    compile_finding,
    evidence_blocks,
    resolve_blocks,
    resolve_quotes,
)
from ax_evaluator.checks import observed_tool_events
from ax_evaluator.comments import comment_complete, publication_blockers
from ax_evaluator.itemwise import (
    OPEN_QUESTION,
    full_bundle,
    judge_items,
    normalize_finding,
    observation_schema,
    refresh_policies,
    validate_task,
)
from ax_evaluator.ledger import QuestionBox
from ax_evaluator.review import prepare, score
from ax_evaluator.snapshot import document
from ax_evaluator.storage import canonical, digest, read_json


class ObservationBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.snapshot, _, rubric = fixture()
        self.criterion = next(i for i in rubric["items"] if i["id"] == "C04")
        self.ref = {"path": "REPORT.md", "start": 1, "end": 1}
        self.raw = {
            "id": "C04",
            "observations": [
                {
                    "id": "central",
                    "status": "supported",
                    "note": "중심 결론 일치",
                    "evidence": [self.ref],
                }
            ],
            "issues": [],
        }

    def compile(self):
        return normalize_finding(
            compile_finding(self.raw, self.criterion, self.snapshot), False
        )

    def test_local_factual_error_requires_revision_without_deduction(self):
        self.raw["issues"] = [
            {
                "check": "central",
                "kind": "factual-error",
                "evidence": [self.ref],
                "feedback": "본문의 수치를 기존 결과에 맞게 고치세요.",
                "rerun_basis": "records-sufficient",
            }
        ]
        finding = self.compile()
        self.assertEqual(
            (finding["status"], finding["feedback_type"], finding["rerun"]),
            ("pass", "revision", "not-needed"),
        )
        self.assertEqual(finding["deduction_reason"], "")

    def test_block_ids_resolve_to_known_sources_without_model_line_arithmetic(self):
        blocks = evidence_blocks(self.snapshot)
        key = next(k for k, ref in blocks.items() if ref["path"] == "REPORT.md")
        self.assertEqual(
            resolve_blocks({"block": key}, blocks),
            {"path": "REPORT.md", "start": 1, "end": 2},
        )
        with self.assertRaisesRegex(ValueError, "Unknown"):
            resolve_blocks({"block": "invented"}, blocks)
        self.assertTrue(all(ref["end"] - ref["start"] < 40 for ref in blocks.values()))

    def test_exact_quotation_computes_lines_and_rejects_paraphrases(self):
        quote = {"path": "REPORT.md", "quote": "Observed results only."}
        self.assertEqual(
            resolve_quotes(quote, self.snapshot),
            {"path": "REPORT.md", "start": 2, "end": 2},
        )
        with self.assertRaises(ValueError):
            resolve_quotes(
                {**quote, "quote": "Observed successful results only."}, self.snapshot
            )

    def test_repeated_quote_requires_more_context_not_guessed_location(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["files"]["REPORT.md"]["text"] += "Observed results only.\n"
        with self.assertRaisesRegex(ValueError, "unique"):
            resolve_quotes(
                {"path": "REPORT.md", "quote": "Observed results only."}, snapshot
            )

    def test_missing_evidence_is_unknown_and_never_zero(self):
        self.raw["observations"][0].update(status="unknown", evidence=[])
        result = self.compile()
        self.assertEqual(
            (result["status"], result["pending_reason"]), ("pending", "evidence")
        )
        self.assertFalse(result["deduction_reason"])

    def test_observed_material_violation_fails_required_item(self):
        self.criterion["category"] = "requirements"
        self.raw["observations"][0]["status"] = "contradicted"
        result = normalize_finding(
            compile_finding(self.raw, self.criterion, self.snapshot)
        )
        self.assertEqual((result["status"], result["severity"]), ("fail", "material"))
        self.assertTrue(result["deduction_reason"])

    def test_optional_improvement_never_becomes_revision(self):
        self.raw["issues"] = [
            {
                "check": "central",
                "kind": "improvement",
                "evidence": [self.ref],
                "feedback": "표를 더 간결하게 정리할 수 있습니다.",
                "rerun_basis": "records-sufficient",
            }
        ]
        result = self.compile()
        self.assertEqual(
            (result["status"], result["feedback_type"]), ("pass", "suggestion")
        )

    def test_invented_checks_or_free_grade_fields_are_rejected(self):
        self.raw["observations"][0]["id"] = "CI-execution-required"
        with self.assertRaises(ValueError):
            self.compile()
        self.raw["observations"][0]["id"] = "central"
        self.raw["status"] = "pass"
        with self.assertRaises(ValueError):
            self.compile()

    def test_claim_without_source_and_unknown_issue_kind_are_rejected(self):
        self.raw["observations"][0]["evidence"] = []
        with self.assertRaises(ValueError):
            self.compile()

        self.raw["observations"][0]["evidence"] = [self.ref]
        self.raw["issues"] = [
            {
                "check": "central",
                "kind": "personal-preference",
                "evidence": [self.ref],
                "feedback": "변경",
                "rerun_basis": "records-sufficient",
            }
        ]
        with self.assertRaises(ValueError):
            self.compile()

    def test_response_uses_fixed_keys_not_model_read_receipts(self):
        snapshot, _, rubric = fixture()
        snapshot["id"] = digest({k: v for k, v in snapshot.items() if k != "id"})
        criterion = next(i for i in rubric["items"] if i["id"] == "I03")
        schema = observation_schema(
            criterion, "item", ["REPORT.md"], rubric["bonus_policy"], []
        )
        self.assertNotIn("uniqueItems", canonical(schema))
        self.assertNotIn("inspected_files", schema["properties"])
        self.assertEqual(
            schema["properties"]["finding"]["properties"]["observations"]["type"],
            "object",
        )
        request = {
            "engine": "itemwise-v3",
            "stage": "item",
            "schema": schema,
            "files": {"REPORT.md": {}},
        }
        result = {
            "inspected_files": ["REPORT.md", "REPORT.md"],
            "questions": [],
            "finding": {},
        }
        with self.assertRaisesRegex(ValueError, "fields"):
            validate_task(
                result, request, full_bundle(snapshot, rubric), snapshot, criterion
            )


class BonusCandidateTest(unittest.TestCase):
    def setUp(self):
        self.snapshot, _, rubric = fixture()
        self.policy = rubric["bonus_policy"]
        self.ref = {"path": "REPORT.md", "start": 2, "end": 2}
        self.candidate = {
            "title": "추가 분석",
            "contribution": "추가 비교와 근거",
            "criterion_id": "",
            "evidence": [self.ref],
        }

    def assessment(self, cid):
        return {
            "id": cid,
            "checks": {k: "supported" for k in BONUS_CHECKS},
            "duplicate_of": "",
            "validation": "추가 구현과 비교 결과 확인",
            "limitations": "제출 표본에 한정",
            "evidence": [self.ref],
            "issues": [],
        }

    def test_candidates_have_artifact_ids_not_title_ids(self):
        first = catalog_candidates([self.candidate], self.policy, self.snapshot)
        renamed = {**self.candidate, "title": "이름을 바꾼 추가 분석"}
        second = catalog_candidates([renamed], self.policy, self.snapshot)
        self.assertEqual(first[0]["id"], second[0]["id"])

    def test_duplicate_artifacts_and_grading_demands_are_not_new_candidates(self):
        with self.assertRaises(ValueError):
            catalog_candidates(
                [self.candidate, {**self.candidate, "title": "또 다른 제목"}],
                self.policy,
                self.snapshot,
            )
        with self.assertRaises(ValueError):
            catalog_candidates(
                [
                    {
                        **self.candidate,
                        "evidence": [{"path": "@spec.md", "start": 1, "end": 1}],
                    }
                ],
                self.policy,
                self.snapshot,
            )

    def test_explicit_criterion_is_present_even_when_feature_absent(self):
        self.policy["explicit_criteria"] = [
            {"id": "E1", "title": "교수자 기준", "rule": "독립 추가 실험", "points": 1}
        ]
        candidates = catalog_candidates([], self.policy, self.snapshot)
        self.assertEqual([v["criterion_id"] for v in candidates], ["E1"])

    def test_eligibility_cannot_invent_candidate_or_skip_checks(self):
        candidates = catalog_candidates([self.candidate], self.policy, self.snapshot)
        value = self.assessment("invented")
        with self.assertRaises(ValueError):
            compile_bonuses([value], candidates, self.snapshot)
        value["id"] = candidates[0]["id"]
        del value["checks"]["validated"]
        with self.assertRaises(ValueError):
            compile_bonuses([value], candidates, self.snapshot)

    def test_unvalidated_extra_never_receives_bonus(self):
        candidates = catalog_candidates([self.candidate], self.policy, self.snapshot)
        value = self.assessment(candidates[0]["id"])
        value["checks"]["validated"] = "unknown"
        self.assertEqual(
            compile_bonuses([value], candidates, self.snapshot)[0]["status"], "pending"
        )
        value["checks"]["beyond_required"] = "contradicted"
        result = normalize_finding(
            compile_bonuses([value], candidates, self.snapshot)[0]
        )
        self.assertEqual((result["status"], result["feedback_type"]), ("fail", "none"))

    def test_optional_defect_still_requests_correction(self):
        candidates = catalog_candidates([self.candidate], self.policy, self.snapshot)
        value = self.assessment(candidates[0]["id"])
        value["checks"]["validated"] = "contradicted"
        value["issues"] = [
            {
                "check": "validated",
                "kind": "factual-error",
                "evidence": [self.ref],
                "feedback": "추가 분석의 잘못된 수치를 수정하세요.",
                "rerun_basis": "records-sufficient",
            }
        ]
        result = normalize_finding(
            compile_bonuses([value], candidates, self.snapshot)[0]
        )
        self.assertEqual(
            (result["status"], result["feedback_type"]), ("fail", "revision")
        )

    def test_semantic_duplicate_marker_prevents_double_award(self):
        candidates = catalog_candidates(
            [
                self.candidate,
                {
                    **self.candidate,
                    "title": "다른 위치의 동일 기여",
                    "evidence": [{"path": "REPORT.md", "start": 1, "end": 1}],
                },
            ],
            self.policy,
            self.snapshot,
        )
        values = [self.assessment(c["id"]) for c in candidates]
        values[1]["duplicate_of"] = values[0]["id"]
        self.assertEqual(
            sum(
                v["status"] == "pass"
                for v in compile_bonuses(values, candidates, self.snapshot)
            ),
            1,
        )


class PolicyAndAuditRegressionTest(unittest.TestCase):
    def test_printed_moves_and_reasoning_are_not_counted_as_calls(self):
        snapshot = {
            "files": {
                "logs/run.txt": {
                    "text": "\n".join(
                        [
                            "  [seller call] reject_proposal({})",
                            "     -> rejected. It is now the buyer turn.",
                            '  "moves": [{"role":"buyer","act":"accept_proposal"}]',
                            "Thinking: I could accept_proposal now",
                        ]
                    )
                }
            }
        }
        facts = observed_tool_events(snapshot)["logs/run.txt"]
        self.assertEqual(facts["observed_call_counts"], {"seller.reject_proposal": 1})
        self.assertIsNone(facts["explicit_error_result_lines"])

    def test_json_results_keep_role_and_explicit_error_flags(self):
        rows = [
            {"event": "tool_call", "role": "seller", "tool": "accept_proposal"},
            {
                "event": "tool_result",
                "role": "seller",
                "tool": "accept_proposal",
                "error": False,
            },
            {"event": "tool_result", "role": "buyer", "tool": "propose", "error": True},
        ]
        snapshot = {
            "files": {
                "logs/run.jsonl": {"text": "\n".join(json.dumps(v) for v in rows)}
            }
        }
        facts = observed_tool_events(snapshot)["logs/run.jsonl"]
        self.assertEqual(facts["observed_call_counts"], {"seller.accept_proposal": 1})
        self.assertEqual(facts["explicit_error_result_lines"], [3])

    def test_unsupported_event_format_is_unknown_not_zero(self):
        snapshot = {"files": {"logs/run.txt": {"text": "No recognized event syntax"}}}
        self.assertIsNone(
            observed_tool_events(snapshot)["logs/run.txt"]["observed_call_counts"]
        )

    def test_bonus_candidates_use_separate_full_logs_and_replay_offline(self):
        from ax_evaluator.itemwise import replay_items

        snapshot, _, rubric = fixture()
        for index in (1, 2):
            snapshot["files"][f"extra{index}.py"] = document(
                f"VALUE = {index}\n".encode()
            )
            snapshot["files"][f"experiments/{index}/logs/run.txt"] = document(
                ("evidence\n" * 3500).encode()
            )
        snapshot["id"] = digest({k: v for k, v in snapshot.items() if k != "id"})
        seen = []

        def transport(config, prompt, schema):
            text, meta = fake_response(prompt, schema)
            raw = json.loads(text)
            packet = json.loads(prompt.split("UNTRUSTED_FULL_FILES_JSON:\n")[1])
            if "candidates" in schema["properties"]:
                raw["candidates"] = [
                    {
                        "title": f"추가 실험 {i}",
                        "contribution": "독립된 추가 분석",
                        "criterion_id": "",
                        "evidence": [
                            {
                                "block": packet[f"extra{i}.py"]["text"]
                                .splitlines()[0]
                                .split(" ", 1)[1]
                            }
                        ],
                        "supporting_files": [f"experiments/{i}/logs/run.txt"],
                    }
                    for i in (1, 2)
                ]
            elif "assessments" in schema["properties"]:
                seen.append(set(packet))
                cid = schema["properties"]["assessments"]["items"]["properties"]["id"][
                    "enum"
                ][0]
                log = next(p for p in packet if p.startswith("experiments/"))
                raw["assessments"] = [
                    {
                        "id": cid,
                        "checks": {k: "supported" for k in BONUS_CHECKS},
                        "duplicate_of": "",
                        "validation": "구현과 로그 확인",
                        "limitations": "제출 표본에 한정",
                        "evidence": [
                            {
                                "block": packet[log]["text"]
                                .splitlines()[0]
                                .split(" ", 1)[1]
                            }
                        ],
                        "issues": [],
                    }
                ]
            return canonical(raw), meta

        with (
            tempfile.TemporaryDirectory() as temp,
            patch("ax_evaluator.itemwise.cli_review", side_effect=transport),
            patch(
                "ax_evaluator.itemwise.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "fixture", ""),
            ),
        ):
            root = Path(temp)
            texts = [f["text"] or "" for f in snapshot["files"].values()]
            bundle = prepare(
                snapshot,
                rubric,
                root / "packet",
                sum(map(len, texts)) + 1,
                max(map(len, texts)) + 1,
            )
            response = judge_items(
                bundle,
                snapshot,
                {"kind": "claude", "model": "fixture"},
                root / "model",
                root / "ledger",
                max_task_chars=40000,
            )
            self.assertFalse(response["provenance"]["metrics"]["failures"])
            self.assertEqual(len(seen), 4)  # Observe and verify each candidate.
            self.assertTrue(
                all(
                    len([p for p in paths if p.startswith("experiments/")]) == 1
                    for paths in seen
                )
            )
            self.assertTrue(response["review"]["bonus_review_complete"])
            self.assertEqual(
                sum(b["status"] == "pass" for b in response["review"]["bonuses"]), 2
            )
            replay = replay_items(
                root / "packet", root / "model", root / "ledger", root / "replayed"
            )
            self.assertEqual(response["review"], replay["review"])

    def test_instructor_links_different_phrasings_and_answers_once(self):
        with tempfile.TemporaryDirectory() as temp:
            box = QuestionBox(temp)
            a = box.register(
                {"policy": "a", "question": "수정본 인정?", "options": ["yes", "no"]},
                "F07",
            )
            b = box.register(
                {
                    "policy": "b",
                    "question": "사후 수정 결과를 인정하나요?",
                    "options": ["no", "yes"],
                },
                "C04",
            )
            box.link([a, b], "instructor")
            box.answer(a, "yes", "instructor")
            self.assertEqual(box.policies("C04")["b"]["answer"]["value"], "yes")
            box.answer(b, "no", "instructor")
            self.assertEqual(box.policies("F07")["a"]["answer"]["value"], "no")
            self.assertEqual(len(box.list()), 2)
            self.assertEqual(box.list()[0]["equivalent_questions"], sorted([a, b]))

    def test_conflicting_policy_answers_are_not_silently_merged(self):
        with tempfile.TemporaryDirectory() as temp:
            box = QuestionBox(temp)
            a = box.register(
                {"policy": "a", "question": "A?", "options": ["yes", "no"]}, "F07"
            )
            b = box.register(
                {"policy": "b", "question": "B?", "options": ["yes", "no"]}, "C04"
            )
            box.answer(a, "yes", "instructor")
            box.answer(b, "no", "instructor")
            with self.assertRaisesRegex(ValueError, "conflicting"):
                box.link([a, b], "instructor")

    def test_teacher_answer_updates_two_frozen_packets_with_no_unrelated_calls(self):
        snapshot, rows, rubric = fixture()
        for row in rows:
            if row["deal_possible"] == "0":
                row["outcome"] = "open"
        set_rows(snapshot, rows)
        snapshot["id"] = digest({k: v for k, v in snapshot.items() if k != "id"})
        with (
            tempfile.TemporaryDirectory() as temp,
            patch(
                "ax_evaluator.itemwise.cli_review", side_effect=fake_response
            ) as transport,
            patch(
                "ax_evaluator.itemwise.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "fixture", ""),
            ),
        ):
            root = Path(temp)
            packets = []
            for index in range(2):
                packet = root / f"original-{index}"
                texts = [f["text"] or "" for f in snapshot["files"].values()]
                bundle = prepare(
                    snapshot,
                    rubric,
                    packet,
                    sum(map(len, texts)) + 1,
                    max(map(len, texts)) + 1,
                )
                judge_items(
                    bundle,
                    snapshot,
                    {"kind": "claude", "model": "fixture"},
                    root / f"run-{index}",
                    root / "ledger",
                )
                packets.append(packet)
            box = QuestionBox(root / "ledger")
            question = next(
                q
                for q in box.list()
                if q["question"]["policy"] == OPEN_QUESTION["policy"]
            )
            box.answer(question["id"], "correct", "instructor")
            transport.reset_mock()
            result = refresh_policies(
                packets,
                root / "ledger",
                root / "updated",
                {"kind": "claude", "model": "fixture"},
            )
            transport.assert_not_called()
            self.assertEqual([r["affected_tasks"] for r in result], [["C02"], ["C02"]])
            for row in result:
                report = read_json(Path(row["packet"]) / "final/evaluation.json")
                self.assertEqual(report["items"]["C02"]["status"], "pass")
                self.assertTrue(comment_complete(report))

    def test_reviewed_means_inspected_even_if_student_has_a_defect(self):
        snapshot, _, rubric = fixture()
        snapshot["id"] = digest({k: v for k, v in snapshot.items() if k != "id"})
        bundle = full_bundle(snapshot, rubric)
        with (
            tempfile.TemporaryDirectory() as temp,
            patch("ax_evaluator.itemwise.cli_review", side_effect=fake_response),
            patch(
                "ax_evaluator.itemwise.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "fixture", ""),
            ),
        ):
            response = judge_items(
                bundle,
                snapshot,
                {"kind": "claude", "model": "fixture"},
                Path(temp) / "run",
                Path(temp) / "ledger",
            )
        review = response["review"]
        review["items"][0]["status"] = "fail"
        self.assertTrue(
            all(
                a["status"] == "reviewed"
                for a in assemble_audits(bundle, review).values()
            )
        )

    def test_transport_failure_is_not_a_student_revision(self):
        snapshot, _, rubric = fixture()
        snapshot["id"] = digest({k: v for k, v in snapshot.items() if k != "id"})
        report = score(full_bundle(snapshot, rubric))
        report["provenance"] = {"metrics": {"failures": [{"task": "F06"}]}}
        self.assertIn(
            {"task": "F06", "kind": "evaluation-failure"}, publication_blockers(report)
        )

    def test_answered_model_question_is_applied_on_refresh(self):
        snapshot, _, rubric = fixture()
        snapshot["id"] = digest({k: v for k, v in snapshot.items() if k != "id"})
        question = {
            "policy": "new",
            "question": "원래 기록의 수정본도 인정하나요?",
            "options": ["yes", "no"],
        }

        def transport(config, prompt, schema):
            text, meta = fake_response(prompt, schema)
            value = json.loads(text)
            if value.get("finding", {}).get("id") == "F07":
                value["questions"] = [question]
            return canonical(value), meta

        with (
            tempfile.TemporaryDirectory() as temp,
            patch("ax_evaluator.itemwise.cli_review", side_effect=transport) as model,
            patch(
                "ax_evaluator.itemwise.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "fixture", ""),
            ),
        ):
            root = Path(temp)
            texts = [f["text"] or "" for f in snapshot["files"].values()]
            bundle = prepare(
                snapshot,
                rubric,
                root / "packet",
                sum(map(len, texts)) + 1,
                max(map(len, texts)) + 1,
            )
            first = judge_items(
                bundle,
                snapshot,
                {"kind": "claude", "model": "fixture"},
                root / "first",
                root / "ledger",
            )
            self.assertEqual(
                next(i for i in first["review"]["items"] if i["id"] == "F07")[
                    "pending_reason"
                ],
                "policy",
            )
            box = QuestionBox(root / "ledger")
            q = next(
                q
                for q in box.list()
                if q["question"]["question"] == question["question"]
            )
            box.answer(q["id"], "yes", "instructor")
            model.reset_mock()
            refresh_policies(
                [root / "packet"],
                root / "ledger",
                root / "updated",
                {"kind": "claude", "model": "fixture"},
            )
            # Only the dependent task, observe and verify.
            self.assertEqual(model.call_count, 2)
            report = read_json(root / "updated/packet-001/final/evaluation.json")
            self.assertEqual(report["items"]["F07"]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
