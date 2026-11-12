"""Guard evidence completeness, rerun decisions and cause-level feedback."""

import copy
import unittest

from test_policy_comments import bonus, complete_fixture

from ax_evaluator.comments import comment_complete, render_comment
from ax_evaluator.review import score, validate_review
from ax_evaluator.storage import digest


def revision(finding, key="run-log-mismatch", feedback="원본 행과 로그를 연결하세요."):
    finding.update(
        feedback_type="revision",
        revision_key=key,
        feedback=feedback,
        severity="material",
        rerun="unknown",
        rerun_basis="missing-records",
        rerun_reason="원본 대응이 복구되지 않으면 추가 실행이 필요합니다.",
    )


class EvidenceReviewTest(unittest.TestCase):
    def test_old_contract_cannot_bypass_new_review_obligations(self):
        snapshot, bundle, review = complete_fixture()
        del bundle["review_contract"]
        bundle["id"] = digest({k: v for k, v in bundle.items() if k != "id"})
        with self.assertRaisesRegex(ValueError, "prepare a new packet"):
            validate_review(review, bundle, snapshot)

    def test_success_audit_missing_or_unsupported_is_rejected(self):
        snapshot, bundle, original = complete_fixture()
        for area in original["success_audit"]:
            with self.subTest(area=area):
                review = copy.deepcopy(original)
                del review["success_audit"][area]
                with self.assertRaisesRegex(ValueError, "three success"):
                    validate_review(review, bundle, snapshot)
                review = copy.deepcopy(original)
                review["success_audit"][area]["evidence"] = []
                with self.assertRaisesRegex(ValueError, "requires source evidence"):
                    validate_review(review, bundle, snapshot)
                review["success_audit"][area]["status"] = "not-applicable"
                with self.assertRaisesRegex(ValueError, "requires source evidence"):
                    validate_review(review, bundle, snapshot)

    def test_unread_judge_blocks_complete_comment_even_with_full_points(self):
        snapshot, bundle, review = complete_fixture()
        review["success_audit"]["judge_logic"].update(
            status="insufficient", reason="판정 코드가 입력에서 생략됨", evidence=[]
        )
        validate_review(review, bundle, snapshot)
        report = score(bundle, review)
        self.assertEqual((report["base_earned"], report["base_max"]), (18, 18))
        self.assertFalse(comment_complete(report))
        self.assertIn("성공 판정 검토", render_comment(report, bundle, snapshot))

    def test_behavior_change_cannot_claim_no_new_experiment(self):
        snapshot, bundle, review = complete_fixture()
        finding = review["items"][0]
        revision(finding, "verifier-return", "검증 후 최종 답 반환을 수정하세요.")
        finding.update(
            rerun_basis="behavior-change",
            rerun="not-needed",
            rerun_reason="수정된 반환 경로의 실행 근거가 필요합니다.",
        )
        with self.assertRaisesRegex(ValueError, "contradicts"):
            validate_review(review, bundle, snapshot)
        finding["rerun"] = "needed"
        validate_review(review, bundle, snapshot)
        self.assertIn(
            "재실험 필요:", render_comment(score(bundle, review), bundle, snapshot)
        )

    def test_missing_records_needs_conditional_or_required_rerun(self):
        snapshot, bundle, review = complete_fixture()
        finding = review["items"][0]
        revision(finding)
        validate_review(review, bundle, snapshot)
        comment = render_comment(score(bundle, review), bundle, snapshot)
        self.assertIn("복구되지 않으면 추가 실행", comment)
        self.assertNotIn("새 모델 실험 없이", comment)
        finding["rerun"] = "not-needed"
        with self.assertRaisesRegex(ValueError, "contradicts"):
            validate_review(review, bundle, snapshot)

    def test_rerun_reason_and_cause_key_are_required(self):
        snapshot, bundle, review = complete_fixture()
        finding = review["items"][0]
        revision(finding)
        finding["rerun_reason"] = " "
        with self.assertRaisesRegex(ValueError, "needs a reason"):
            validate_review(review, bundle, snapshot)
        finding["rerun_reason"] = "기존 원본 확인 필요"
        finding["revision_key"] = ""
        with self.assertRaisesRegex(ValueError, "revision_key"):
            validate_review(review, bundle, snapshot)

    def test_same_cause_emitted_once_without_losing_separate_score_entries(self):
        snapshot, bundle, review = complete_fixture()
        for key in ("F09", "C04"):
            finding = next(i for i in review["items"] if i["id"] == key)
            revision(finding)
            finding.update(
                status="fail",
                deduction_reason="Required results unsupported" if key == "F09" else "",
            )
        validate_review(review, bundle, snapshot)
        report = score(bundle, review)
        self.assertEqual((report["base_earned"], report["base_max"]), (17, 18))
        self.assertEqual(report["items"]["C04"]["status"], "fail")
        self.assertEqual(
            render_comment(report, bundle, snapshot).count(
                "- 원본 행과 로그를 연결하세요."
            ),
            1,
        )

    def test_same_key_cannot_silently_drop_a_different_correction(self):
        snapshot, bundle, review = complete_fixture()
        revision(review["items"][0])
        revision(review["items"][1], feedback="성공률도 수정하세요.")
        with self.assertRaisesRegex(ValueError, "consolidated feedback"):
            validate_review(review, bundle, snapshot)
        review["items"][1]["revision_key"] = "separate-cause"
        validate_review(review, bundle, snapshot)
        comment = render_comment(score(bundle, review), bundle, snapshot)
        self.assertIn("- 원본 행과 로그", comment)
        self.assertIn("- 성공률도 수정", comment)

    def test_bonus_and_base_share_one_correction_with_stronger_rerun(self):
        snapshot, bundle, review = complete_fixture()
        revision(review["items"][0])
        review["bonuses"] = [bonus()]
        revision(review["bonuses"][0])
        review["bonuses"][0].update(
            rerun="needed",
            rerun_basis="behavior-change",
            rerun_reason="수정된 추가 검증 경로는 새 실행이 필요합니다.",
        )
        validate_review(review, bundle, snapshot)
        comment = render_comment(score(bundle, review), bundle, snapshot)
        self.assertEqual(comment.count("- 원본 행과 로그를 연결하세요."), 1)
        self.assertIn("재실험 필요:", comment)
        self.assertIn("원본 대응이 복구되지 않으면", comment)
        self.assertIn("수정된 추가 검증 경로", comment)

    def test_review_key_cannot_hide_an_automatic_failure(self):
        snapshot, bundle, review = complete_fixture()
        revision(review["items"][0], key="automatic-1")
        report = score(bundle, review)
        automatic = next(f for f in report["items"].values() if f["origin"] == "code")
        automatic.update(status="fail", reason="자동 검사에서 필수 파일 누락")
        comment = render_comment(report, bundle, snapshot)
        self.assertIn("- 원본 행과 로그", comment)
        self.assertIn("- 자동 검사에서 필수 파일 누락", comment)


if __name__ == "__main__":
    unittest.main()
