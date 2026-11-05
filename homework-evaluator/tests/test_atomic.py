"""Atomic grading must preserve real failures, uncertainty and code-owned facts."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_checks import fixture
from test_itemwise import fake_response

from ax_evaluator.assessment import compile_finding, evidence_blocks
from ax_evaluator.atomic import fixed_facts, source_index, validate_atoms
from ax_evaluator.facts import json_log_structure
from ax_evaluator.itemwise import full_bundle, judge_items, replay_items, validate_task
from ax_evaluator.review import finalize
from ax_evaluator.snapshot import document
from ax_evaluator.storage import canonical, digest, read_json


class AtomicTest(unittest.TestCase):
    def test_factual_question_cannot_create_an_undeclared_grader_policy(self):
        criterion = next(i for i in self.rubric["items"] if i["id"] == "C04")
        request = {
            "engine": "itemwise-v3",
            "verification_policy": "atomic-facts-declared-policies-v2",
            "schema": {"properties": {"questions": {}, "finding": {}}},
        }
        raw = {
            "questions": [
                {
                    "policy": "invented.fact",
                    "question": "서버가 상태를 저장하는가?",
                    "options": ["yes", "no"],
                }
            ],
            "finding": {},
        }
        with self.assertRaisesRegex(ValueError, "only select declared policies"):
            validate_task(
                raw,
                request,
                full_bundle(self.snapshot, self.rubric),
                self.snapshot,
                criterion,
            )

    def test_only_declared_atomic_policy_waits_and_answer_reuses_other_facts(self):
        from ax_evaluator.ledger import QuestionBox

        criterion = next(i for i in self.rubric["items"] if i["id"] == "I02")
        question = {
            "policy": "intent.scope",
            "question": "명시한 표본 범위의 관측 해석을 인정하나요?",
            "options": ["allow", "disallow"],
        }
        criterion["assessment"]["policy_questions"] = [question]

        def transport(config, prompt, schema):
            text, meta = fake_response(prompt, schema)
            raw = json.loads(text)
            if raw["finding"]["id"] == "I02":
                raw["questions"] = [question]
            return canonical(raw), meta

        self.model.side_effect = transport
        first = self.run_grade()
        finding = next(i for i in first["review"]["items"] if i["id"] == "I02")
        self.assertEqual(
            (finding["status"], finding["pending_reason"]), ("pending", "policy")
        )
        self.assertFalse(first["provenance"]["metrics"]["failures"])
        box = QuestionBox(self.root / "ledger")
        entry = next(
            q for q in box.list() if q["question"]["question"] == question["question"]
        )
        box.answer(entry["id"], "allow", "instructor")
        self.model.reset_mock()
        updated = self.run_grade("answered")
        self.assertEqual(self.model.call_count, 1)
        self.assertEqual(
            next(i for i in updated["review"]["items"] if i["id"] == "I02")["status"],
            "pass",
        )

    def test_generic_atomic_template_keeps_one_scored_requirement(self):
        from ax_evaluator.review import ROOT, score

        self.rubric = read_json(ROOT / "rubrics/review-only.example.json")
        self.snapshot["metadata"]["week"] = self.rubric["week"]
        self.snapshot["id"] = digest(
            {k: v for k, v in self.snapshot.items() if k != "id"}
        )
        response = self.run_grade()
        bundle = full_bundle(self.snapshot, self.rubric)
        self.assertNotIn("json_log_structure", bundle["checks"])
        self.assertFalse(response["provenance"]["metrics"]["failures"])
        result = score(bundle, response["review"])
        self.assertEqual((result["base_earned"], result["base_max"]), (1, 1))
        self.assertEqual(set(result["qualitative_ids"]), {"C1", "I1"})

    def test_global_code_proofs_do_not_expand_the_models_visible_evidence(self):
        self.snapshot["files"]["logs/other.jsonl"] = document(b"code-inspected log\n")
        self.snapshot["id"] = digest(
            {k: v for k, v in self.snapshot.items() if k != "id"}
        )
        criterion = next(i for i in self.rubric["items"] if i["id"] == "C03")
        block = {v["path"]: k for k, v in evidence_blocks(self.snapshot).items()}
        request = {
            "engine": "itemwise-v3",
            "schema": {"properties": {"questions": {}, "finding": {}}},
            "files": {"REPORT.md": {}},
            "context": {
                "atomic_ids": ["count_tool_calls", "interrupted_attempts"],
                "fixed_observations": {
                    "count_tool_calls": {
                        "value": True,
                        "note": "Code inspected all logs.",
                        "evidence": [
                            {"path": "logs/other.jsonl", "start": 1, "end": 1}
                        ],
                    }
                },
            },
        }
        raw = {
            "questions": [],
            "finding": {
                "id": "C03",
                "facts": {
                    "interrupted_attempts": {
                        "value": True,
                        "note": "Inspected supplied report.",
                        "evidence": [{"block": block["REPORT.md"]}],
                    }
                },
            },
        }
        bundle = full_bundle(self.snapshot, self.rubric)
        validate_task(raw, request, bundle, self.snapshot, criterion)
        raw["finding"]["facts"]["interrupted_attempts"]["evidence"] = [
            {"block": block["logs/other.jsonl"]}
        ]
        with self.assertRaisesRegex(ValueError, "not included"):
            validate_task(raw, request, bundle, self.snapshot, criterion)

    def test_cached_partial_keeps_valid_facts_without_implicitly_retrying(self):
        def transport(config, prompt, schema):
            text, meta = fake_response(prompt, schema)
            raw = json.loads(text)
            if "implementation_claims" in raw["finding"]["facts"]:
                raw["finding"]["facts"]["implementation_claims"].update(
                    value=False,
                    claim_quote="Invented claim",
                    counter_quote="Invented counterexample",
                )
            return canonical(raw), meta

        self.model.side_effect = transport
        first = self.run_grade()
        self.model.reset_mock()
        cached = self.run_grade("cached")
        self.assertEqual(self.model.call_count, 0)
        self.assertEqual(cached["review"], first["review"])
        self.assertEqual(
            cached["provenance"]["metrics"]["atomic_decisions"],
            first["provenance"]["metrics"]["atomic_decisions"],
        )
        self.assertTrue(cached["provenance"]["metrics"]["failures"])

    def test_invalid_claim_preserves_valid_leaves_and_retry_only_asks_invalid_leaf(
        self,
    ):
        def transport(config, prompt, schema):
            text, meta = fake_response(prompt, schema)
            raw = json.loads(text)
            if "implementation_claims" in raw["finding"]["facts"]:
                raw["finding"]["facts"]["implementation_claims"].update(
                    value=False,
                    claim_quote="Invented claim",
                    counter_quote="Invented counterexample",
                )
            return canonical(raw), meta

        self.model.side_effect = transport
        first = self.run_grade()
        facts = first["provenance"]["metrics"]["atomic_decisions"]
        self.assertIs(facts["C04.central_agreement"], True)
        self.assertIs(facts["C04.numeric_claims"], True)
        self.assertIsNone(facts["C04.implementation_claims"])
        self.model.side_effect = lambda c, p, s: fake_response(p, s)
        self.model.reset_mock()
        retried = self.run_grade("retry", True)
        self.assertEqual(self.model.call_count, 1)
        fields = self.model.call_args.args[-1]["properties"]["finding"]["properties"][
            "facts"
        ]["properties"]
        self.assertEqual(set(fields), {"implementation_claims"})
        self.assertFalse(retried["provenance"]["metrics"]["failures"])
        from ax_evaluator.review import prepare

        texts = [v["text"] or "" for v in self.snapshot["files"].values()]
        prepare(
            self.snapshot,
            self.rubric,
            self.root / "packet",
            sum(len(t) + 1 for t in texts),
            max(map(len, texts)) + 1,
        )
        # Later successful work must not change replay of the earlier incomplete run.
        old = replay_items(
            self.root / "packet",
            self.root / "first",
            self.root / "ledger",
            self.root / "old-replay",
        )
        self.assertEqual(old["review"], first["review"])
        self.assertIsNone(
            old["provenance"]["metrics"]["atomic_decisions"][
                "C04.implementation_claims"
            ]
        )
        self.assertTrue(old["provenance"]["metrics"]["failures"])

    def test_fabricated_report_claim_cannot_create_a_revision(self):
        criterion = next(i for i in self.rubric["items"] if i["id"] == "C04")
        ref = {"path": "REPORT.md", "start": 1, "end": 2}
        facts = {
            a["id"]: {
                "value": True,
                "note": "record",
                "evidence": [ref],
                **(
                    {"claim_quote": "", "counter_quote": ""}
                    if a["source"] == "semantic-claim"
                    else {}
                ),
            }
            for a in criterion["assessment"]["atoms"]
        }
        facts["implementation_claims"].update(
            value=False,
            claim_quote="The report never said this.",
            counter_quote="Observed results only.",
        )
        with self.assertRaisesRegex(ValueError, "exact quotes"):
            compile_finding({"id": "C04", "facts": facts}, criterion, self.snapshot)

    def test_json_counts_are_positive_scoped_facts_not_inferred_zeros(self):
        from test_checks import set_rows

        snapshot, rows, _ = fixture()
        row = rows[0]
        set_rows(snapshot, [row])
        events = [
            {"event": "meta", "condition": row["condition"], "run": row["run"]},
            {"event": "episode_start", "scenario": row["scenario"]},
            {"event": "host_turn", "role": "buyer"},
        ]
        for tool in [
            "get_negotiation",
            "propose",
            "get_negotiation",
            "accept_proposal",
        ]:
            args = {"negotiation_id": "one"}
            if tool == "propose":
                args["price"] = 20
            events.extend(
                [
                    {"event": "tool_call", "tool": tool, "arguments": args},
                    {
                        "event": "tool_result",
                        "tool": tool,
                        "result": {"isError": False},
                    },
                ]
            )
        events.append({"event": "episode_result", **row})
        snapshot["files"] = {
            k: v for k, v in snapshot["files"].items() if not k.startswith("logs/")
        }
        path = "logs/explicit.jsonl"
        snapshot["files"][path] = document(
            ("\n".join(json.dumps(e) for e in events) + "\n").encode()
        )
        facts = json_log_structure(snapshot)
        self.assertEqual(
            facts["matched_counts"],
            {"tool_calls": True, "turns": True, "refused_calls": True},
        )
        # A call outside the recorded episode cannot be silently discarded.
        events.append(
            {
                "event": "tool_call",
                "tool": "get_negotiation",
                "arguments": {"negotiation_id": "one"},
            }
        )
        snapshot["files"][path] = document(
            ("\n".join(json.dumps(e) for e in events) + "\n").encode()
        )
        self.assertFalse(any(json_log_structure(snapshot)["matched_counts"].values()))

    def test_json_pairing_cannot_certify_a_different_roles_response(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["files"] = {
            k: v for k, v in snapshot["files"].items() if not k.startswith("logs/")
        }
        events = [
            {
                "event": "tool_call",
                "role": "buyer",
                "tool": "get_negotiation",
                "arguments": {"negotiation_id": "one"},
            },
            {
                "event": "tool_result",
                "role": "seller",
                "tool": "get_negotiation",
                "result": {},
            },
        ]
        snapshot["files"]["logs/roles.jsonl"] = document(
            ("\n".join(json.dumps(e) for e in events) + "\n").encode()
        )
        facts = json_log_structure(snapshot)
        self.assertFalse(facts["results_paired"])
        self.assertFalse(any(facts["matched_counts"].values()))

    def setUp(self):
        self.snapshot, _, self.rubric = fixture(atomic=True)
        self.snapshot["id"] = digest(
            {k: v for k, v in self.snapshot.items() if k != "id"}
        )
        self.rubric["bonus_policy"].update(open_ended=False, explicit_criteria=[])
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(
            patch("ax_evaluator.itemwise.subprocess.run")
        ).return_value.stdout = "fixture"
        self.model = self.enterContext(
            patch(
                "ax_evaluator.itemwise.cli_review",
                side_effect=lambda c, p, s: fake_response(p, s),
            )
        )

    def run_grade(self, name="first", retry_failed=False):
        self.snapshot["id"] = digest(
            {k: v for k, v in self.snapshot.items() if k != "id"}
        )
        return judge_items(
            full_bundle(self.snapshot, self.rubric),
            self.snapshot,
            {"kind": "codex", "model": "fixture"},
            self.root / name,
            self.root / "ledger",
            workers=2,
            retry_failed=retry_failed,
        )

    def test_leaf_contracts_preserve_scoring_and_model_never_chooses_points(self):
        response = self.run_grade()
        self.assertEqual(response["provenance"]["metrics"]["failures"], [])
        self.assertEqual(len(response["provenance"]["metrics"]["atomic_decisions"]), 72)
        self.assertTrue(all(i["status"] == "pass" for i in response["review"]["items"]))
        bundle = full_bundle(self.snapshot, self.rubric)
        self.assertEqual(
            sum(i["category"] == "requirements" for i in bundle["rubric"]["items"]), 18
        )
        for call in self.model.call_args_list:
            self.assertEqual(call.args[-1]["properties"]["questions"]["maxItems"], 0)
            schema = call.args[-1]["properties"]["finding"]["properties"]
            self.assertLessEqual(len(schema["facts"]["properties"]), 4)
            self.assertNotIn("status", schema)

    def test_one_guard_counterexample_fails_only_its_existing_requirement(self):
        def transport(config, prompt, schema):
            text, meta = fake_response(prompt, schema)
            value = json.loads(text)
            facts = value["finding"]["facts"]
            if "buyer_accept_limit" in facts:
                facts["buyer_accept_limit"]["value"] = False
            return canonical(value), meta

        self.model.side_effect = transport
        response = self.run_grade()
        findings = {i["id"]: i for i in response["review"]["items"]}
        self.assertEqual(findings["F08"]["status"], "fail")
        self.assertEqual(findings["F07"]["status"], "pass")
        self.assertIn("buyer accept", findings["F08"]["feedback"])

    def test_unknown_is_not_a_counterexample_and_correction_does_not_deduct(self):
        self.snapshot["files"]["REPORT.md"] = document(
            b"# Report\nThe server always returns success.\n"
        )
        server_block = next(
            k
            for k, v in evidence_blocks(self.snapshot).items()
            if v["path"] == "server.py"
        )

        def transport(config, prompt, schema):
            text, meta = fake_response(prompt, schema)
            value = json.loads(text)
            for key, fact in value["finding"]["facts"].items():
                if key == "implementation_claims":
                    fact.update(
                        value=False,
                        claim_quote="The server always returns success.",
                        counter_quote=(
                            "raise RuntimeError('STUDENT CODE MUST NEVER RUN')"
                        ),
                    )
                    fact["evidence"].append({"block": server_block})
                if key == "seller_accept_limit":
                    fact["value"] = None
            return canonical(value), meta

        self.model.side_effect = transport
        response = self.run_grade()
        findings = {i["id"]: i for i in response["review"]["items"]}
        self.assertEqual(
            (findings["C04"]["status"], findings["C04"]["feedback_type"]),
            ("pass", "revision"),
        )
        self.assertEqual(findings["C04"]["deduction_reason"], "")
        self.assertEqual(findings["F08"]["status"], "pending")

    def test_saved_replay_has_zero_calls_and_identical_atomic_values(self):
        first = self.run_grade()
        self.model.reset_mock()
        second = self.run_grade("second")
        self.model.assert_not_called()
        self.assertEqual(first["review"], second["review"])
        self.assertEqual(
            first["provenance"]["metrics"]["atomic_decisions"],
            second["provenance"]["metrics"]["atomic_decisions"],
        )
        from ax_evaluator.review import prepare

        texts = [v["text"] or "" for v in self.snapshot["files"].values()]
        prepare(
            self.snapshot,
            self.rubric,
            self.root / "packet",
            sum(len(t) + 1 for t in texts),
            max(map(len, texts)) + 1,
        )
        offline = replay_items(
            self.root / "packet",
            self.root / "first",
            self.root / "ledger",
            self.root / "offline",
        )
        self.assertEqual(first["review"], offline["review"])
        self.assertEqual(
            first["provenance"]["metrics"]["atomic_decisions"],
            offline["provenance"]["metrics"]["atomic_decisions"],
        )
        finalize(self.root / "packet", offline, self.root / "final")

    def test_failed_batch_keeps_other_facts_and_requires_explicit_retry(self):
        calls = []

        def transport(config, prompt, schema):
            keys = schema["properties"]["finding"]["properties"]["facts"]["properties"]
            calls.append(tuple(keys))
            if "buyer_accept_limit" in keys:
                raise ValueError("unavailable observation")
            return fake_response(prompt, schema)

        self.model.side_effect = transport
        first = self.run_grade()
        metrics = first["provenance"]["metrics"]
        self.assertTrue(metrics["failures"])
        self.assertIs(metrics["atomic_decisions"]["F08.negotiation_binding"], True)
        self.assertIsNone(metrics["atomic_decisions"]["F08.buyer_accept_limit"])
        self.model.side_effect = lambda c, p, s: fake_response(p, s)
        self.model.reset_mock()
        second = self.run_grade("retry", True)
        self.assertEqual(self.model.call_count, 1)
        self.assertFalse(second["provenance"]["metrics"]["failures"])

    def test_code_owned_facts_cannot_be_overridden_by_model(self):
        def transport(config, prompt, schema):
            text, meta = fake_response(prompt, schema)
            value = json.loads(text)
            if value["finding"]["id"] == "C03":
                self.assertNotIn(
                    "summary_equality",
                    schema["properties"]["finding"]["properties"]["facts"][
                        "properties"
                    ],
                )
                value["finding"]["facts"]["summary_equality"] = {
                    "value": False,
                    "note": "forged",
                    "evidence": [],
                }
            return canonical(value), meta

        self.model.side_effect = transport
        response = self.run_grade()
        self.assertTrue(response["provenance"]["metrics"]["failures"])
        self.assertIs(
            response["provenance"]["metrics"]["atomic_decisions"][
                "C03.summary_equality"
            ],
            True,
        )

    def test_atomic_contract_rejects_new_grade_fields_and_wrong_values(self):
        criterion = next(i for i in self.rubric["items"] if i["id"] == "I05")
        facts = {
            a["id"]: {
                "value": True,
                "note": "record",
                "evidence": [{"path": "REPORT.md", "start": 1, "end": 1}],
            }
            for a in criterion["assessment"]["atoms"]
        }
        for value in (1, "supported", []):
            facts["injection_log_reference"]["value"] = value
            with self.assertRaises(ValueError):
                compile_finding({"id": "I05", "facts": facts}, criterion, self.snapshot)
        broken = copy.deepcopy(criterion)
        broken["assessment"]["atoms"][0]["parent"] = "new-requirement"
        with self.assertRaises(ValueError):
            validate_atoms(broken)

    def test_unsupported_logs_never_become_automatic_missing_records(self):
        criterion = next(i for i in self.rubric["items"] if i["id"] == "C03")
        self.assertEqual(
            fixed_facts(
                criterion,
                self.snapshot,
                {
                    "log_comparison": {
                        "unparsed_logs": ["logs/custom.txt"],
                        "unmatched_csv_lines": [2],
                    }
                },
            ),
            {},
        )

    def test_ast_index_is_source_data_and_never_executes_student_code(self):
        self.snapshot["files"]["server.py"] = document(
            b"raise RuntimeError('do not execute')\n"
            b"def move(x):\n    if x < 0:\n        return 'error'\n"
        )
        indexed = source_index(self.snapshot)["server.py"]
        self.assertEqual(indexed[0]["branches"][0]["condition"], "x < 0")
