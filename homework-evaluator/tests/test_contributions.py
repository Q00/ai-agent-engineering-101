"""Connected duplicate nominations earn at most one supported contribution."""

import unittest

from ax_evaluator.contributions import resolve_duplicates


class ContributionsTest(unittest.TestCase):
    def fixture(self):
        catalog = [
            {"id": key, "criterion_id": "named" if key == "B" else ""} for key in "ABC"
        ]
        bonuses = [
            {
                "id": key,
                "contribution_key": key,
                "status": "fail",
                "severity": "none",
                "feedback_type": "suggestion",
                "feedback": "duplicate",
                "validation": "validated",
                "limitations": "same contribution",
            }
            for key in "ABC"
        ]
        raw = [
            {
                "id": key,
                "duplicate_of": target,
                "checks": {
                    k: "supported"
                    for k in [
                        "beyond_required",
                        "distinct",
                        "implemented",
                        "validated",
                        "meaningful",
                    ]
                },
            }
            for key, target in [("A", "B"), ("B", "A"), ("C", "")]
        ]
        for value in raw[:2]:
            value["checks"]["distinct"] = "contradicted"
        bonuses[2]["status"] = "pass"
        return catalog, bonuses, raw

    def test_cycles_choose_one_eligible_named_representative_and_keep_other_work(self):
        catalog, bonuses, raw = self.fixture()
        result, groups = resolve_duplicates(bonuses, raw, catalog)
        self.assertEqual([b["id"] for b in result if b["status"] == "pass"], ["B", "C"])
        self.assertEqual(groups, [{"members": ["A", "B"], "representative": "B"}])
        self.assertEqual(result[0]["contribution_key"], "B")

    def test_declared_equivalence_does_not_upgrade_unvalidated_or_unknown_work(self):
        catalog, bonuses, raw = self.fixture()
        raw[0]["checks"]["validated"] = "unknown"
        raw[1]["checks"]["implemented"] = "contradicted"
        result, groups = resolve_duplicates(bonuses, raw, catalog)
        self.assertEqual([b["id"] for b in result if b["status"] == "pass"], ["C"])
        self.assertEqual(groups, [])

    def test_permutation_does_not_change_group_owner(self):
        catalog, bonuses, raw = self.fixture()
        left, _ = resolve_duplicates(bonuses, raw, catalog)
        right, _ = resolve_duplicates(
            list(reversed(bonuses)), list(reversed(raw)), catalog
        )
        self.assertEqual(
            sorted((b["id"], b["status"], b["contribution_key"]) for b in left),
            sorted((b["id"], b["status"], b["contribution_key"]) for b in right),
        )
