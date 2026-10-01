"""Check that failed attempts and repeat consistency are reported honestly."""
import asyncio
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import condition_study as study


class StudyTests(unittest.TestCase):
    def test_fact_comparison_keeps_boolean_missing_and_numeric_distinctions(self):
        expected = {"number": 2, "flag": True}
        first = study.canonical_facts({"number": 2, "flag": True, "extra": "ignored"}, expected)
        self.assertEqual(first, study.canonical_facts({"number": 2.0, "flag": True}, expected))
        self.assertNotEqual(study.fingerprint(first),
                            study.fingerprint(study.canonical_facts({"number": 2, "flag": 1}, expected)))
        self.assertEqual(study.canonical_facts({}, expected)["flag"], {"missing": True})
        self.assertIsNone(study.canonical_facts(None, expected))

    def test_failed_runs_stay_in_denominator_and_wrong_agreement_is_separate(self):
        template = {"passed": False, "checks_passed": 0, "checks_total": 12, "calls": 20,
                    "elapsed_seconds": 10, "reported_cost_usd": 0.01, "awards": {"C": 1},
                    "canonical_facts": {"wrong": 42}, "root_award": "C", "max_depth": 1,
                    "proposal_rejections": 0, "c_high_bids": 1, "c_proposals": 1}
        metrics = []
        for condition in study.CONDITIONS:
            for block in range(3):
                item = dict(copy.deepcopy(template), condition=condition)
                if block == 2:
                    item.update(canonical_facts=None, reported_cost_usd=None)
                metrics.append(item)
        groups = study.aggregate(metrics)
        for group in groups.values():
            self.assertEqual((group["attempts"], group["passed"], group["checks_total"]), (3, 0, 36))
            self.assertEqual((group["runs_with_facts"], group["distinct_fact_vectors"]), (2, 1))
            self.assertEqual(group["runs_with_cost"], 2)

    def test_timeout_preserves_partial_trace_without_fabricating_result(self):
        with tempfile.TemporaryDirectory(dir=study.ROOT) as folder:
            root = Path(folder)
            (root / "logs").mkdir()
            log = root / "logs/interrupted.jsonl"
            original = '{"event":"call_start","phase":"propose","worker":"A"}\n{"event":'
            log.write_text(original)
            attempt = {"run_id": "interrupted", "condition": "baseline", "block": 1,
                       "exit_code": -15, "timed_out": True, "wall_seconds": 600}
            with patch.object(study, "ROOT", root):
                metric, setting = study.inspect_run(attempt, {"expected": 1})
            self.assertIsNone(setting)
            self.assertEqual(metric["status"], "timeout")
            self.assertFalse(metric["passed"])
            self.assertFalse(metric["trace_passed"])
            self.assertEqual(metric["calls"], 1)
            self.assertIsNone(metric["canonical_facts"])
            self.assertIsNone(metric["reported_cost_usd"])
            self.assertEqual(log.read_text(), original)


class ScheduleTests(unittest.IsolatedAsyncioTestCase):
    async def test_serial_study_preserves_all_attempts_order_and_eight_gaps(self):
        schedule = [{"block": block, "condition": condition, "run_id": f"{block}-{condition}"}
                    for block, conditions in enumerate(study.BLOCKS, 1) for condition in conditions]
        active, peak, order = 0, 0, []
        original_sleep = asyncio.sleep

        async def fake_run(folder, condition, block, run_id):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            order.append(run_id)
            await original_sleep(0)
            active -= 1
            return {"run_id": run_id, "exit_code": 1 if condition == "homogeneous" else 0}

        with patch.object(study, "attempt_run", side_effect=fake_run), \
             patch.object(study.asyncio, "sleep", new_callable=AsyncMock) as gaps:
            attempts = await study.run_schedule(None, schedule, serial=True, gap_seconds=15)
        self.assertEqual(peak, 1)
        self.assertEqual(order, [item["run_id"] for item in schedule])
        self.assertEqual(len(attempts), 9)
        self.assertEqual(sum(item["exit_code"] == 1 for item in attempts), 3)
        self.assertEqual(gaps.await_count, 8)
        self.assertTrue(all(call.args == (15,) for call in gaps.await_args_list))

    async def test_changed_source_stops_before_more_api_calls(self):
        schedule = [{"block": block, "condition": "baseline", "run_id": str(block)} for block in range(1, 4)]
        with patch.object(study, "frozen_sources", side_effect=[{"a": "old"}, {"a": "new"}]), \
             patch.object(study, "attempt_run", new_callable=AsyncMock) as run:
            with self.assertRaisesRegex(ValueError, "source changed"):
                await study.run_schedule(None, schedule, serial=True, source_hashes={"a": "old"})
        self.assertEqual(run.await_count, 1)


if __name__ == "__main__":
    unittest.main()
