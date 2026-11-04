import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from improvement_notices import publish


class NoticeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = Path(self.tmp.name) / ".market"
        self.md.mkdir()
        self.obs = dict(at=100000, valid=True, self_evidence="local_acp_v1", deals=20,
                        bankruptcies=0, rank=18, reviews=20, stars=4.3)

    def emit(self, events=(), **changes):
        with contextlib.redirect_stderr(io.StringIO()):
            return publish(self.md, dict(self.obs, **changes), events)

    def test_baseline_and_small_changes_are_quiet(self):
        self.assertEqual(self.emit(), [])
        self.assertEqual(self.emit(at=107200, rank=17, stars=4.35, reviews=25), [])

    def test_growth_waits_two_hours_then_reports_once(self):
        self.emit()
        self.assertEqual(self.emit(at=101800, rank=15, reviews=30, stars=4.5), [])
        result = self.emit(at=107200, rank=15, reviews=30, stars=4.5)
        self.assertEqual(len(result), 1)
        self.assertIn("18→15", result[0]["message"])
        self.assertEqual(self.emit(at=107200, rank=15, reviews=30, stars=4.5), [])

    def test_keep_immediate_and_deduplicated_after_reload(self):
        event = dict(event="keep", experiment="trial-1", comparison={
            "before": {"deals_per_hour": 3, "gross_per_hour": 100},
            "after": {"deals_per_hour": 4, "gross_per_hour": 110}})
        self.assertEqual(len(self.emit([event])), 1)
        self.assertEqual(self.emit([event]), [])
        self.assertTrue((self.md.parent / "IMPROVEMENTS.md").exists())

    def test_start_evaluation_and_rollback_not_claimed_as_improvement(self):
        self.assertEqual(self.emit([dict(event=name) for name in ("start", "evaluation", "rollback")]), [])

    def test_bankruptcy_resets_baseline_without_growth_notice(self):
        self.emit()
        self.assertEqual(self.emit(at=107200, rank=1, bankruptcies=1, stars=5, reviews=40), [])

    def test_stale_and_wrong_evidence_are_quiet(self):
        self.emit()
        self.assertEqual(self.emit(at=107200, valid=False, rank=1), [])
        self.assertEqual(self.emit(at=107200, self_evidence="public", rank=1), [])

    def test_rating_requires_new_reviews(self):
        self.emit()
        self.assertEqual(self.emit(at=107200, stars=4.7, reviews=24), [])

    def test_notification_failure_does_not_prevent_policy_publication(self):
        import strategy_controller as controller
        from market_state import read
        with patch.object(controller, "observation", return_value={"at": 100000, "valid": False}), \
                patch("improvement_notices.publish", side_effect=OSError("notification unavailable")):
            result = controller.run(self.md, now=100000, force=True)
        self.assertEqual(result["status"], "waiting_for_fresh_r3_data")
        self.assertTrue(read(self.md / "strategy-policy.json")["controls"]["restock_paused"])


if __name__ == "__main__":
    unittest.main()
