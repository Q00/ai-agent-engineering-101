"""warranty_trial.liability() reuses parsed files while their (mtime_ns, size) is unchanged (the user's 11-11 choice in the
Orchestrator session, about 19:2x KST: "제안대로 진행해줘"). Its results must equal the old full parse in every case."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from market_state import read, task_file
import warranty_trial as wt

NOW = 1794400000.0


def old_liability(md, now):
    """The function before the cache, copied as it was."""
    md = Path(md)
    local = read(md / "local-settlements.json", {}).get("records", {})
    settled = {r.get("unit"): r.get("ts", 0) for r in local.values() if r.get("round") == 3}
    total, signed = 0, set()
    for source in (md / "signed-offers").glob("*.json"):
        offer = read(source)
        task = offer.get("task")
        if task:
            signed.add(task)
        hours = offer.get("warranty_hours") or 0
        if hours <= 0:
            continue
        status = read(task_file(md, "checkout-events", task), {}).get("status") if task else None
        if status in ("refused", "canceled", "expired"):
            continue
        start = max(offer["issued_at"], settled.get(offer["unit"], 0))
        if status in ("processing", "uncertain") or now < start+hours*3600+600:
            total += offer["price"]
    state = read(md / "warranty-trial.json", {})
    total += sum(a["quote"] for task, a in state.get("assignments", {}).items()
                 if a.get("hours") and task not in signed and now < a["expires_at"])
    return total


def put(path, value, mtime_ns=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf8")
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))


class LiabilityCache(unittest.TestCase):
    def setUp(self):
        wt._PARSED.clear()
        self.tmp = TemporaryDirectory()
        self.md = Path(self.tmp.name)
        offers = [("t1", "u1", 0, 100), ("t2", "u2", 6, 200), ("t3", "u3", 6, 300), ("t4", "u4", 72, 400), ("t5", "u5", 6, 500), (None, "u6", 6, 600)]
        for task, unit, hours, price in offers:
            put(self.md / "signed-offers" / f"{unit}.json",
                {"task": task, "unit": unit, "warranty_hours": hours, "price": price, "issued_at": NOW - 3 * 3600})
        put(task_file(self.md, "checkout-events", "t2"), {"status": "completed"})
        put(task_file(self.md, "checkout-events", "t3"), {"status": "refused"})
        put(task_file(self.md, "checkout-events", "t5"), {"status": "processing"})
        put(self.md / "local-settlements.json", {"records": {"a": {"round": 3, "unit": "u2", "ts": NOW - 7 * 3600}}})
        put(self.md / "warranty-trial.json", {"assignments": {"t9": {"hours": 6, "quote": 50, "expires_at": NOW + 60},
                                                             "t1": {"hours": 6, "quote": 70, "expires_at": NOW + 60}}})

    def tearDown(self):
        self.tmp.cleanup()
        wt._PARSED.clear()

    def check(self, now=NOW):
        expected = old_liability(self.md, now)
        self.assertEqual(wt.liability(self.md, now), expected)
        return expected

    def test_same_result_as_the_old_parse_first_and_cached(self):
        first = self.check()
        # u2 completed and still inside its 6 h cover (issued 3 h ago), u3 refused, u1 no warranty, t1's trial quote already signed.
        self.assertEqual(first, 200 + 400 + 500 + 600 + 50)
        self.assertEqual(self.check(), first)
        self.assertEqual(self.check(NOW + 100 * 3600), 500)  # only the processing one; every cover and the trial quote have ended

    def test_a_second_call_parses_no_unchanged_file(self):
        wt.liability(self.md, NOW)
        def only_missing(path, default=None):  # a file that does not exist is still looked up, as before; nothing is parsed
            if Path(path).exists():
                raise AssertionError(f"parsed again: {path}")
            return default
        with patch.object(wt, "read", side_effect=only_missing):
            self.assertEqual(wt.liability(self.md, NOW), old_liability(self.md, NOW))

    def test_new_rewritten_and_removed_files_are_seen(self):
        self.check()
        put(self.md / "signed-offers" / "u7.json", {"task": "t7", "unit": "u7", "warranty_hours": 6, "price": 700, "issued_at": NOW})
        self.check()
        event = task_file(self.md, "checkout-events", "t5")
        before = os.stat(event).st_mtime_ns
        put(event, {"status": "canceled"}, mtime_ns=before + 1000)  # same size class, newer mtime
        self.assertEqual(self.check(), 200 + 400 + 600 + 50 + 700)
        (self.md / "signed-offers" / "u4.json").unlink()
        self.assertEqual(self.check(), 200 + 600 + 50 + 700)
        self.assertFalse(any(k.endswith("u4.json") for k in wt._PARSED))

    def test_a_rewrite_with_the_same_mtime_but_a_new_size_is_seen(self):
        self.check()
        path = self.md / "signed-offers" / "u4.json"
        stamp = os.stat(path).st_mtime_ns
        put(path, {"task": "t4", "unit": "u4", "warranty_hours": 72, "price": 4000, "issued_at": NOW - 3 * 3600}, mtime_ns=stamp)
        self.check()

    def test_a_file_that_does_not_parse_raises_as_before(self):
        self.check()
        (self.md / "signed-offers" / "bad.json").write_text("{", encoding="utf8")
        with self.assertRaises(ValueError):
            old_liability(self.md, NOW)
        with self.assertRaises(ValueError):
            wt.liability(self.md, NOW)
        (self.md / "signed-offers" / "bad.json").unlink()
        self.check()

    def test_missing_folders_and_files_mean_nothing_reserved(self):
        with TemporaryDirectory() as empty:
            self.assertEqual(wt.liability(empty, NOW), old_liability(empty, NOW))
            self.assertEqual(wt.liability(empty, NOW), 0)


if __name__ == "__main__":
    unittest.main()
