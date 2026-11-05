"""Policy, fair scoring and publication boundaries without external writes."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_review import review_fixture

from ax_evaluator.comments import (
    comment_complete,
    publish,
    render_comment,
    source_links,
    write_comment,
)
from ax_evaluator.policy import interview, validate_policy
from ax_evaluator.review import build_bundle, render, score, validate_review
from ax_evaluator.storage import digest


def complete_fixture():
    snapshot, bundle, review = review_fixture()
    for item in review["items"]:
        item.update(
            status="pass",
            reason="Core requirement supported",
            evidence=[{"path": "REPORT.md", "start": 1, "end": 1}],
            feedback_type="strength",
            feedback="기록을 잘 남겼습니다.",
            pending_reason="",
            rerun="not-needed",
            rerun_basis="records-sufficient",
            rerun_reason="Existing records suffice for this fixture.",
        )
    for audit in review["success_audit"].values():
        audit.update(
            status="reviewed",
            reason="Fixture evidence inspected",
            evidence=[{"path": "REPORT.md", "start": 1, "end": 1}],
        )
    review["bonus_review_complete"] = True
    return snapshot, bundle, review


def bonus(criterion="", key="extra"):
    return dict(
        id="B1",
        title="Extra",
        status="pass",
        contribution="Beyond requirements",
        validation="Recorded evidence",
        limitations="Small sample",
        evidence=[{"path": "REPORT.md", "start": 1, "end": 1}],
        feedback_type="strength",
        feedback="추가 검증이 있습니다.",
        severity="none",
        rerun="not-needed",
        rerun_basis="not-applicable",
        rerun_reason="No correction requested.",
        revision_key="",
        criterion_id=criterion,
        contribution_key=key,
    )


class PolicyTest(unittest.TestCase):
    def test_old_packets_are_not_silently_rescored_with_new_semantics(self):
        snapshot, bundle, review = complete_fixture()
        del bundle["rubric"]["scoring_policy"]
        bundle["id"] = digest({k: v for k, v in bundle.items() if k != "id"})
        with self.assertRaisesRegex(ValueError, "prepare a new packet"):
            score(bundle, review)
        with self.assertRaisesRegex(ValueError, "prepare a new packet"):
            validate_review(review, bundle, snapshot)

    def test_bonus_weights_other_than_one_are_rejected(self):
        _, bundle, _ = complete_fixture()
        for points in (0, 2, True, 1.0):
            policy = copy.deepcopy(bundle["rubric"]["bonus_policy"])
            policy["open_points"] = points
            with self.assertRaisesRegex(ValueError, "exactly 1"):
                validate_policy(policy)
            policy["open_points"] = 1
            policy["explicit_criteria"] = [
                dict(id="E1", title="Extra", rule="Evidence", points=points)
            ]
            with self.assertRaisesRegex(ValueError, "exactly 1"):
                validate_policy(policy)

    def test_qualitative_failure_keeps_score_and_revision(self):
        snapshot, bundle, review = complete_fixture()
        item = next(i for i in review["items"] if i["id"] == "C04")
        item.update(
            status="fail",
            feedback_type="revision",
            revision_key="fixture-cause",
            severity="material",
            feedback="추가 분석의 계산식을 수정하세요.",
        )
        validate_review(review, bundle, snapshot)
        report = score(bundle, review)
        self.assertEqual((report["base_earned"], report["base_max"]), (18, 18))
        self.assertIn("계산식을 수정", render_comment(report, bundle, snapshot))
        self.assertIn(
            "C04 보고서·PR·실제 구현의 일치 | fail | 무배점", render(report, bundle)
        )
        item["deduction_reason"] = "Qualitative deduction"
        with self.assertRaisesRegex(ValueError, "Qualitative"):
            validate_review(review, bundle, snapshot)

    def test_qualitative_pending_does_not_change_score_but_blocks_publication(self):
        snapshot, bundle, review = complete_fixture()
        item = next(i for i in review["items"] if i["id"] == "C04")
        item.update(
            status="pending", pending_reason="evidence", feedback_type="confirmation"
        )
        validate_review(review, bundle, snapshot)
        report = score(bundle, review)
        self.assertEqual(report["base_possible_range"], [18, 18])
        self.assertEqual(report["base_pending"], 0)
        self.assertEqual(report["qualitative_pending"], 1)
        self.assertFalse(comment_complete(report))

    def test_more_qualitative_reviews_do_not_increase_denominator(self):
        snapshot, bundle, review = complete_fixture()
        rubric = copy.deepcopy(bundle["rubric"])
        qualitative = next(i for i in rubric["items"] if i["id"] == "C04")
        finding = next(i for i in review["items"] if i["id"] == "C04")
        for number in range(8):
            rubric["items"].append({**qualitative, "id": f"Q{number}"})
            review["items"].append({**finding, "id": f"Q{number}"})
        bundle = build_bundle(snapshot, rubric)
        validate_review(review, bundle, snapshot)
        report = score(bundle, review)
        self.assertEqual((report["base_earned"], report["base_max"]), (18, 18))
        self.assertEqual(len(report["qualitative_ids"]), 11)

    def test_missing_required_deliverable_deducts_once_despite_related_feedback(self):
        snapshot, bundle, review = complete_fixture()
        for key in ("C04", "F09"):
            item = next(i for i in review["items"] if i["id"] == key)
            item.update(
                status="fail",
                feedback_type="revision",
                revision_key="fixture-cause",
                severity="material",
                feedback="필수 결과표를 수정하세요.",
                deduction_reason="Required results table materially incorrect"
                if key == "F09"
                else "",
            )
        validate_review(review, bundle, snapshot)
        report = score(bundle, review)
        self.assertEqual((report["base_earned"], report["base_max"]), (17, 18))

    def test_scored_requirements_need_assignment_source(self):
        snapshot, bundle, _ = complete_fixture()
        rubric = copy.deepcopy(bundle["rubric"])
        del rubric["items"][0]["source_requirement"]
        with self.assertRaisesRegex(ValueError, "assignment source"):
            build_bundle(snapshot, rubric)

    def test_interview_without_explicit_rules_keeps_meaningful_extras(self):
        answers = iter(["n", ""])
        policy = interview(lambda _: next(answers))
        self.assertEqual(policy["explicit_criteria"], [])
        self.assertTrue(policy["open_ended"])
        self.assertIsNone(policy["cap"])

    def test_explicit_interview_records_rule_points_and_cap(self):
        answers = iter(
            ["y", "Robustness", "Independent failure tests with logs", "", "4"]
        )
        policy = interview(lambda _: next(answers))
        self.assertEqual(policy["explicit_criteria"][0]["points"], 1)
        self.assertEqual(policy["cap"], 4)
        self.assertTrue(policy["open_ended"])

    def test_minor_defect_cannot_deduct_but_pass_can_request_revision(self):
        snapshot, bundle, review = complete_fixture()
        item = review["items"][0]
        item.update(
            severity="minor",
            feedback_type="revision",
            revision_key="fixture-cause",
            feedback="표현을 수정하세요.",
        )
        validate_review(review, bundle, snapshot)
        self.assertEqual(score(bundle, review)["base_earned"], 18)
        item.update(status="fail", deduction_reason="A typo")
        with self.assertRaisesRegex(ValueError, "material defect"):
            validate_review(review, bundle, snapshot)

    def test_explicit_points_and_semantic_duplicate_guard(self):
        snapshot, bundle, review = complete_fixture()
        rubric = copy.deepcopy(bundle["rubric"])
        rubric["bonus_policy"]["explicit_criteria"] = [
            dict(id="E1", title="Robustness", rule="Independent tests", points=1)
        ]
        bundle = build_bundle(snapshot, rubric)
        review["bonuses"] = [bonus("E1")]
        validate_review(review, bundle, snapshot)
        self.assertEqual(score(bundle, review)["bonus_earned"], 1)
        review["bonuses"].append({**bonus(), "id": "B2", "title": "Different name"})
        with self.assertRaisesRegex(ValueError, "twice"):
            validate_review(review, bundle, snapshot)
        review["bonuses"] = []
        with self.assertRaisesRegex(ValueError, "Every explicit"):
            validate_review(review, bundle, snapshot)

    def test_other_assignment_review_only_has_no_week05_checks(self):
        snapshot, bundle, _ = complete_fixture()
        rubric = copy.deepcopy(bundle["rubric"])
        rubric.update(week="week-06", checker="review-only")
        snapshot["metadata"]["week"] = "week-06"
        snapshot["id"] = digest({k: v for k, v in snapshot.items() if k != "id"})
        rubric.pop("audit_groups", None)
        rubric["items"] = [{**rubric["items"][0], "id": "R1", "mode": "review"}]
        other = build_bundle(snapshot, rubric)
        self.assertEqual(list(other["checks"]["items"]), ["R1"])
        self.assertEqual(other["checks"]["aggregates"], {})


class CommentsTest(unittest.TestCase):
    def test_student_comment_does_not_link_internal_packet_files(self):
        finding = {
            "evidence": [
                {"path": "@history.txt", "start": 1, "end": 2},
                {"path": "REPORT.md", "start": 10, "end": 12},
            ]
        }
        links = source_links(finding, {"source": "local"})
        self.assertNotIn("@history", links)
        self.assertIn("REPORT.md:10", links)

    def test_material_revision_survives_pass_minor_stays_in_detail(self):
        snapshot, bundle, review = complete_fixture()
        review["items"][0].update(
            feedback_type="revision",
            revision_key="fixture-cause",
            severity="material",
            feedback="주요 해석을 수정하세요.",
        )
        review["items"][1].update(
            feedback_type="revision",
            revision_key="fixture-cause",
            severity="minor",
            feedback="typo-only",
        )
        comment = render_comment(score(bundle, review), bundle, snapshot)
        self.assertIn("주요 해석", comment)
        self.assertNotIn("typo-only", comment)
        self.assertIn("새 모델 실험 없이", comment)
        self.assertNotIn("20점", comment)

    def test_policy_question_is_not_student_revision(self):
        snapshot, bundle, review = complete_fixture()
        review["items"][0].update(
            status="pending",
            pending_reason="policy",
            feedback_type="confirmation",
            feedback="교수자 정책 질문",
        )
        comment = render_comment(score(bundle, review), bundle, snapshot)
        self.assertIn("교수자 확인 사항", comment)
        self.assertNotIn("교수자 정책 질문", comment)

    def make_final(self, root, incomplete=False):
        snapshot, bundle, review = complete_fixture()
        snapshot["metadata"].update(
            source="github",
            repository="example/course",
            pr=7,
            head_sha="abc123",
            student_id="123",
            week="week-05",
        )
        snapshot["id"] = digest({k: v for k, v in snapshot.items() if k != "id"})
        bundle = build_bundle(snapshot, bundle["rubric"])
        report = score(bundle, None if incomplete else review)
        write_comment(root, report, bundle, snapshot)

    def test_preview_is_offline_and_incomplete_or_modified_drafts_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_final(root)
            with patch("ax_evaluator.comments.github") as api:
                self.assertEqual(publish(root)["action"], "preview-only")
                api.assert_not_called()
            (root / "pr-comment.md").write_text("changed")
            with self.assertRaisesRegex(ValueError, "changed"):
                publish(root, write=True)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_final(root, incomplete=True)
            with self.assertRaisesRegex(ValueError, "Incomplete"):
                publish(root, write=True)

    def test_stale_pr_blocks_write_and_existing_own_comment_is_updated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_final(root)
            with patch(
                "ax_evaluator.comments.github",
                return_value={"head": {"sha": "different"}, "state": "open"},
            ) as api:
                with self.assertRaisesRegex(ValueError, "head changed"):
                    publish(root, write=True)
                self.assertEqual(api.call_count, 1)
            marker = "<!-- ax-homework-evaluator:example/course:7:week-05 -->"
            responses = [
                {"head": {"sha": "abc123"}, "state": "open"},
                {"login": "grader"},
                [
                    {"id": 4, "user": {"login": "grader"}, "body": marker + "\nold"},
                    {"id": 5, "user": {"login": "student"}, "body": marker + "\nother"},
                ],
                {"head": {"sha": "abc123"}, "state": "open"},
                {"html_url": "https://example.invalid/comment/4"},
            ]
            with patch("ax_evaluator.comments.github", side_effect=responses) as api:
                self.assertEqual(publish(root, write=True)["action"], "updated")
                args = api.call_args.args
                self.assertEqual(
                    args[:2], ("repos/example/course/issues/comments/4", "PATCH")
                )

    def test_create_then_identical_comment_needs_no_second_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_final(root)
            head = {"head": {"sha": "abc123"}, "state": "open"}
            with patch(
                "ax_evaluator.comments.github",
                side_effect=[
                    head,
                    {"login": "grader"},
                    [],
                    head,
                    {"html_url": "https://example.invalid/new"},
                ],
            ) as api:
                self.assertEqual(publish(root, write=True)["action"], "created")
                self.assertEqual(
                    api.call_args.args[:2],
                    ("repos/example/course/issues/7/comments", "POST"),
                )
                body = api.call_args.args[2]["body"]
            existing = {
                "user": {"login": "grader"},
                "body": body,
                "html_url": "https://example.invalid/new",
            }
            with patch(
                "ax_evaluator.comments.github",
                side_effect=[
                    head,
                    {"login": "grader"},
                    [existing],
                ],
            ) as api:
                self.assertEqual(publish(root, write=True)["action"], "unchanged")
                self.assertEqual(api.call_count, 3)


if __name__ == "__main__":
    unittest.main()
