import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from improvement_notices import publish
from market_state import read


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
        self.assertEqual(len(list((self.md.parent / "logs/notification-errors").glob("*.json"))), 1)

    def test_profit_goal_reports_profit_rank_and_profit_growth(self):
        self.emit(objective="seller_profit_rank_1", profit_rank=15, profit=1000000)
        result = self.emit(at=107200, deals=30, objective="seller_profit_rank_1", profit_rank=13, profit=1200000, rank=30)
        self.assertEqual(len(result), 1)
        self.assertIn("순이익 순위 15→13", result[0]["message"])
        self.assertIn("1,000,000→1,200,000", result[0]["message"])

    def test_profit_growth_requires_confirmed_deals_and_threshold(self):
        self.emit(objective="seller_profit_rank_1", profit_rank=15, profit=1000000)
        result = self.emit(at=107200, deals=29, objective="seller_profit_rank_1", profit_rank=15, profit=1200000)
        self.assertEqual(result, [])
        result = self.emit(at=114400, deals=40, objective="seller_profit_rank_1", profit_rank=15, profit=1240000)
        self.assertEqual(result, [])

    def test_overall_rank_does_not_trigger_profit_goal_improvement(self):
        self.emit(objective="seller_profit_rank_1", profit_rank=15, profit=1000000)
        self.assertEqual(self.emit(at=107200, objective="seller_profit_rank_1", profit_rank=15, profit=1000000, rank=1), [])

    def test_objective_change_resets_baseline_and_profit_first_is_immediate(self):
        self.emit()
        self.assertEqual(self.emit(at=107200, objective="seller_profit_rank_1", profit_rank=15, profit=1000000, rank=1), [])
        result = self.emit(at=107260, objective="seller_profit_rank_1", profit_rank=1, profit=1000000)
        self.assertEqual(len(result), 1)
        self.assertIn("1위", result[0]["message"])
        self.assertEqual(self.emit(at=107300, objective="seller_profit_rank_1", profit_rank=1, profit=1000000), [])

    def test_status_is_visible_every_thirty_minutes_without_fake_improvements(self):
        self.assertEqual(self.emit(), [])
        self.emit(at=100060)
        folder = self.md.parent / "logs/monitor-notices"
        self.assertEqual(len(list(folder.glob("*.json"))), 1)
        self.emit(at=101800)
        self.assertEqual(len(list(folder.glob("*.json"))), 2)
        self.assertTrue((self.md.parent / "MONITORING-NOTICES.md").exists())
        self.assertFalse((self.md.parent / "IMPROVEMENTS.md").exists())

    def test_stale_status_is_not_reported_as_normal(self):
        self.emit(valid=False)
        self.assertIn("자료 확인 대기", read(self.md / "latest-monitor-notice.json")["message"])

    def test_failed_delivery_does_not_consume_keep_deduplication(self):
        event = dict(event="keep", experiment="retry", comparison={"before": {}, "after": {}})
        with patch("improvement_notices.write", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.emit([event])
        self.assertEqual(len(self.emit([event])), 1)

    def test_controller_interval_skip_still_checks_notice_delivery(self):
        import strategy_controller as controller
        from test_strategy_controller import observation
        from market_state import write
        state = controller.initial()
        state.update(checked_at=100000, phase="cooldown")
        write(self.md / "strategy-state.json", state)
        with patch.object(controller, "observation", return_value=observation(100060)), \
                patch("improvement_notices.publish") as delivery:
            controller.run(self.md, now=100060)
        delivery.assert_called_once()
        self.assertEqual(delivery.call_args.kwargs["phase"], "cooldown")


if __name__ == "__main__":
    unittest.main()
