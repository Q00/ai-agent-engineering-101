"""Tests for the additional one-axis Plan-then-Execute experiments."""
import unittest
from unittest.mock import patch

import harness_plan_execute


class FakePlannerChat:
    systems = []

    def __init__(self, system, meter, tools=True):
        self.system = system
        self.tools = tools
        self.messages = []
        self.systems.append(system)

    def add_user(self, text):
        self.messages.append(text)

    def send(self):
        if not self.tools:
            return harness_plan_execute.Reply('["inspect", "count", "answer"]', [])
        return harness_plan_execute.Reply("Answer: 14:00", [])

    def run_tools(self, reply, log):
        raise AssertionError("this fake never requests a tool")


class ExperimentVariantTests(unittest.TestCase):
    def setUp(self):
        FakePlannerChat.systems.clear()

    @patch.object(harness_plan_execute, "Chat", FakePlannerChat)
    def test_short_plan_changes_only_the_planner_instruction(self):
        answer, _, replans = harness_plan_execute.run_plan_execute_short_plan(
            "task", log=lambda _: None
        )

        self.assertEqual("Answer: 14:00", answer)
        self.assertEqual(0, replans)
        self.assertIn("at most 3 steps", FakePlannerChat.systems[0])
        self.assertEqual(harness_plan_execute.SYSTEM_EXEC, FakePlannerChat.systems[1])

    @patch.object(harness_plan_execute, "Chat", FakePlannerChat)
    def test_no_replan_keeps_the_baseline_planner_instruction(self):
        harness_plan_execute.run_plan_execute_no_replan("task", log=lambda _: None)

        self.assertEqual(harness_plan_execute.SYSTEM_PLAN, FakePlannerChat.systems[0])


if __name__ == "__main__":
    unittest.main()
