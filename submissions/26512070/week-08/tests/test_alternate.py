"""Method 'alternate' (user 11/7 10:4x): restock windows take arms A and B in turn; an approval starts it."""
import os
import tempfile
import unittest
from pathlib import Path

from harness import experiment
from harness.wisdom import success_line

CHANGE = {"playbook": {"restock": {"food_cap": 4}}}


class AlternateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("HARNESS_OUT")
        os.environ["HARNESS_OUT"] = str(Path(self.tmp.name) / "out")
        experiment.register("x1", CHANGE, "revenue", {"direction": "up", "by": 0.15}, 3, "alternate", now=1000)
        experiment.approve("x1", "test", now=1000)

    def tearDown(self):
        os.environ.pop("HARNESS_OUT", None) if self.old is None else os.environ.__setitem__("HARNESS_OUT", self.old)
        self.tmp.cleanup()

    def test_windows_take_turns_and_b_gets_the_change(self):
        arms = [experiment.assign_window(m, now=2000) for m in (100, 120, 140, 160)]
        self.assertEqual([a["x1"] for a, _ in arms], ["A", "B", "A", "B"])
        self.assertEqual(arms[0][1], {})
        self.assertEqual(arms[1][1], CHANGE)

    def test_the_same_window_keeps_its_arm(self):
        first = experiment.assign_window(100, now=2000)
        again = experiment.assign_window(101, now=2010)           # a second plan in the same window
        self.assertEqual(first, again)
        self.assertEqual(len(experiment.ledger()["experiments"]["x1"]["windows"]), 1)

    def test_nothing_outside_the_period_or_without_a_window(self):
        self.assertEqual(experiment.assign_window(100, now=1000 + 3 * 3600 + 1), ({}, {}))
        self.assertEqual(experiment.assign_window(None, now=2000), ({}, {}))

    def test_success_line_from_text(self):
        doc = {"evaluation": {"success_if": "B revenue per window >= 1.15x A"}, "expected": {"direction": "up"}}
        self.assertEqual(success_line(doc), {"direction": "up", "by": 0.15})
        doc = {"evaluation": {"success_if": "at least 5% above A"}, "expected": {"direction": "up"}}
        self.assertEqual(success_line(doc), {"direction": "up", "by": 0.05})
        with self.assertRaises(ValueError):
            success_line({"evaluation": {"success_if": "better"}, "expected": {}})


if __name__ == "__main__":
    unittest.main()
