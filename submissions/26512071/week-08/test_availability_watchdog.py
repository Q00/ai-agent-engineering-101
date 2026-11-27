"""Availability watchdog: start the exact main task only when no owned supervisor exists; never kill or duplicate."""
import json, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import availability_watchdog as wd

NOW = datetime(2026, 11, 6, 8, 0, 0, tzinfo=timezone.utc)


def obs(**kw):
    base = dict(task_state="Ready", mutex_held=False, supervisor_alive=False, heartbeat_age_s=None,
                paused=False, last_start_age_s=None)
    base.update(kw)
    return wd.Observation(**base)


class DecideTests(unittest.TestCase):
    def test_stopped_without_owned_identity_starts(self):
        self.assertEqual(wd.decide(obs()), (wd.START, "stopped_no_owner"))

    def test_live_owner_never_duplicates_even_if_task_ready(self):
        for o in (obs(mutex_held=True), obs(supervisor_alive=True), obs(task_state="Ready", mutex_held=True)):
            self.assertEqual(wd.decide(o)[0], wd.NONE)

    def test_running_task_without_owner_only_ignore_new_start(self):
        self.assertEqual(wd.decide(obs(task_state="Running")), (wd.START, "running_no_owner_ignore_new"))

    def test_stale_heartbeat_with_live_owner_is_logged_not_recovered(self):
        self.assertEqual(wd.decide(obs(mutex_held=True, heartbeat_age_s=wd.STALE_S + 1)),
                         (wd.NONE, "owned_live_stale_heartbeat"))
        self.assertEqual(wd.decide(obs(mutex_held=True, heartbeat_age_s=5)), (wd.NONE, "owned_live"))

    def test_remote_offline_api_failure_expired_pilot_never_restart_live_tree(self):
        # Those conditions never reach the decision: only ownership, task state and heartbeat do.
        for age in (None, 5, 10_000):
            self.assertEqual(wd.decide(obs(supervisor_alive=True, heartbeat_age_s=age))[0], wd.NONE)
        self.assertNotIn("kill", " ".join(n for n in dir(wd)).lower())

    def test_disabled_or_unknown_task_and_pause_are_respected(self):
        self.assertEqual(wd.decide(obs(task_state="Disabled")), (wd.NONE, "task_disabled"))
        self.assertEqual(wd.decide(obs(task_state=None)), (wd.NONE, "task_unknown"))
        self.assertEqual(wd.decide(obs(paused=True)), (wd.NONE, "paused"))

    def test_start_backoff_bounds_retries(self):
        self.assertEqual(wd.decide(obs(last_start_age_s=30)), (wd.NONE, "start_backoff"))
        self.assertEqual(wd.decide(obs(last_start_age_s=wd.MIN_START_GAP_S + 1))[0], wd.START)


class StatusTests(unittest.TestCase):
    def test_heartbeat_age_from_status(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "runtime-status.json"
            p.write_text(json.dumps({"supervisor_pid": 42, "supervisor_started": "2026-11-06T16:24:11+09:00",
                                     "updated_at": "2026-11-06T16:59:00+09:00"}))
            st = wd.read_status(p)
            self.assertEqual(wd.heartbeat_age(st, NOW), 60)
            self.assertEqual(st["supervisor_pid"], 42)

    def test_missing_or_corrupt_status_is_empty(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "s.json"
            self.assertEqual(wd.read_status(p), {})
            p.write_text("{not json")
            self.assertEqual(wd.read_status(p), {})
            self.assertIsNone(wd.heartbeat_age({}, NOW))

    def test_supervisor_identity_requires_matching_creation_time(self):
        started = "2026-11-06T16:24:11+09:00"
        created = datetime(2026, 11, 6, 7, 24, 11, 400000, tzinfo=timezone.utc)
        self.assertTrue(wd.same_identity(created, started))
        self.assertFalse(wd.same_identity(created + timedelta(minutes=5), started))   # pid reused
        self.assertFalse(wd.same_identity(None, started))
        self.assertFalse(wd.same_identity(created, None))


class RunOnceTests(unittest.TestCase):
    def run_once(self, state, mutex, alive, d):
        with patch.object(wd, "task_state", return_value=state), patch.object(wd, "mutex_held", return_value=mutex), \
             patch.object(wd, "supervisor_alive", return_value=alive), \
             patch.object(wd, "start_task", return_value=0) as start:
            rec = wd.run_once(Path(d), Path(d) / "missing-status.json", now=NOW)
        return rec, start

    def test_start_called_with_exact_task_and_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            rec, start = self.run_once("Ready", False, False, d)
            start.assert_called_once_with(wd.MAIN_TASK)
            self.assertEqual(rec["action"], wd.START)
            health = json.loads((Path(d) / "watchdog-health.json").read_text())
            self.assertEqual(health["reason"], "stopped_no_owner")
            self.assertTrue(health["ts"].endswith("Z"))
            # second run inside the gap does not start again
            rec2, start2 = self.run_once("Ready", False, False, d)
            start2.assert_not_called()
            self.assertEqual(rec2["reason"], "start_backoff")

    def test_live_owner_no_start(self):
        with tempfile.TemporaryDirectory() as d:
            rec, start = self.run_once("Ready", True, False, d)
            start.assert_not_called()
            self.assertEqual(rec["action"], wd.NONE)


class LogTests(unittest.TestCase):
    def test_log_rotates_and_is_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "watchdog.log"
            for _ in range(400):
                wd.append_log(log, "x" * 100, max_bytes=4000)
            self.assertLessEqual(log.stat().st_size, 4000 + 200)
            self.assertTrue((Path(d) / "watchdog.log.1").exists())
            self.assertFalse((Path(d) / "watchdog.log.2").exists())


if __name__ == "__main__":
    unittest.main()
