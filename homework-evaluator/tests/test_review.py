"""Ensure model opinions cannot silently corrupt scores or evidence."""

import copy
import tempfile
import unittest
from pathlib import Path

from test_checks import fixture

from ax_evaluator.review import (
    build_bundle,
    finalize,
    prepare,
    score,
    template_for,
    validate_review,
)
from ax_evaluator.storage import digest, read_json


def review_fixture():
    snapshot, _, rubric = fixture()
    snapshot["id"] = digest(snapshot)
    rubric["bonus_policy"]["cap"] = 3
    bundle = build_bundle(snapshot, rubric)
    review = template_for(bundle)
    return snapshot, bundle, review


class ReviewTest(unittest.TestCase):
    def test_same_inputs_give_identical_bundle_and_scores(self):
        snapshot, bundle, _ = review_fixture()
        self.assertEqual(bundle, build_bundle(snapshot, bundle["rubric"]))
        self.assertEqual(score(bundle)["base_earned"], 7)
        self.assertEqual(score(bundle)["base_pending"], 11)

    def test_model_cannot_change_deterministic_items(self):
        snapshot, bundle, review = review_fixture()
        review["items"][0]["id"] = "F01"
        with self.assertRaises(ValueError):
            validate_review(review, bundle, snapshot)

    def test_pass_without_evidence_is_rejected(self):
        snapshot, bundle, review = review_fixture()
        review["items"][0]["status"] = "pass"
        with self.assertRaises(ValueError):
            validate_review(review, bundle, snapshot)

    def test_missing_or_duplicate_criteria_are_rejected(self):
        snapshot, bundle, review = review_fixture()
        review["items"].pop()
        with self.assertRaises(ValueError):
            validate_review(review, bundle, snapshot)
        review = template_for(bundle)
        review["items"][-1] = review["items"][0]
        with self.assertRaises(ValueError):
            validate_review(review, bundle, snapshot)

    def test_nonexistent_and_unseen_lines_are_rejected(self):
        snapshot, bundle, review = review_fixture()
        item = review["items"][0]
        item["status"] = "pass"
        item["pending_reason"] = ""
        item["evidence"] = [{"path": "REPORT.md", "start": 100, "end": 100}]
        with self.assertRaises(ValueError):
            validate_review(review, bundle, snapshot)
        bundle = build_bundle(snapshot, bundle["rubric"], max_chars=1)
        item["evidence"] = [{"path": "REPORT.md", "start": 1, "end": 1}]
        with self.assertRaises(ValueError):
            validate_review(review, bundle, snapshot)

    def test_bonus_cap_and_base_failures_remain_separate(self):
        snapshot, bundle, review = review_fixture()
        item = review["items"][0]
        item.update(
            status="fail",
            feedback_type="revision",
            revision_key="fixture-cause",
            severity="material",
            deduction_reason="Core requirement missing",
            pending_reason="",
            evidence=[{"path": "REPORT.md", "start": 1, "end": 1}],
        )
        review["bonuses"] = [
            {
                "id": f"B{index}",
                "criterion_id": "",
                "contribution_key": f"extra-{index}",
                "severity": "none",
                "rerun": "not-needed",
                "rerun_basis": "not-applicable",
                "rerun_reason": "No correction requested.",
                "revision_key": "",
                "title": f"contribution {index}",
                "status": "pass",
                "contribution": "a distinct additional question",
                "validation": "evidence",
                "limitations": "limited sample",
                "feedback_type": "strength",
                "feedback": "추가 근거를 제공함",
                "evidence": [{"path": "REPORT.md", "start": 1, "end": 1}],
            }
            for index in range(5)
        ]
        validate_review(review, bundle, snapshot)
        report = score(bundle, review)
        self.assertEqual(report["bonus_earned"], 3)
        self.assertEqual(report["items"][item["id"]]["status"], "fail")
        self.assertEqual(report["base_pending"], 10)

    def test_same_extra_cannot_be_counted_twice_by_title(self):
        snapshot, bundle, review = review_fixture()
        bonus = {
            "id": "B1",
            "criterion_id": "",
            "contribution_key": "same",
            "severity": "none",
            "rerun": "unknown",
            "rerun_basis": "undetermined",
            "rerun_reason": "Evidence needed.",
            "revision_key": "",
            "title": "same",
            "status": "pending",
            "contribution": "x",
            "validation": "x",
            "limitations": "x",
            "feedback_type": "confirmation",
            "feedback": "확인 필요",
            "evidence": [],
        }
        review["bonuses"] = [bonus, {**bonus, "id": "B2"}]
        with self.assertRaises(ValueError):
            validate_review(review, bundle, snapshot)

    def test_offline_end_to_end_and_response_binding(self):
        snapshot, bundle, review = review_fixture()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle = prepare(snapshot, bundle["rubric"], root / "prepared")
            response = {
                "bundle_id": bundle["id"],
                "review": review,
                "provenance": {"kind": "human", "reviewer": "test"},
            }
            report = finalize(root / "prepared", response, root / "final")
            self.assertEqual(report["base_pending"], 11)
            self.assertIn("사람 검토용", (root / "final/feedback.md").read_text())
            self.assertEqual(
                read_json(root / "final/evaluation.json")["provenance"]["kind"], "human"
            )
            response["bundle_id"] = "wrong"
            with self.assertRaises(ValueError):
                finalize(root / "prepared", response, root / "other")

    def test_packet_tampering_is_detected(self):
        snapshot, bundle, review = review_fixture()
        changed = copy.deepcopy(bundle)
        changed["checks"]["items"]["F01"]["status"] = "fail"
        with self.assertRaises(ValueError):
            validate_review(review, changed, snapshot)


if __name__ == "__main__":
    unittest.main()
