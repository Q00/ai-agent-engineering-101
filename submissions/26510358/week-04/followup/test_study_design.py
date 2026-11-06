"""Check the factors held fixed in each supplemental three-format comparison."""
import unittest

from run_followup import ORDERS, TERMINATION_POLICY
from negotiation import COMMON, CONDITIONS, FORMAT, ROLE, system_prompt


class StudyDesignTests(unittest.TestCase):
    def test_common_policy_and_roles_do_not_depend_on_format(self):
        for role, limit in (("buyer", 180), ("seller", 120)):
            expected = ROLE[role].format(item="a bicycle", limit=limit) + COMMON
            for condition in CONDITIONS:
                self.assertEqual(system_prompt(role, "a bicycle", limit, condition),
                                 expected + FORMAT[condition])
                self.assertEqual(system_prompt(role, "a bicycle", limit, condition,
                                               TERMINATION_POLICY),
                                 expected + TERMINATION_POLICY + FORMAT[condition])

    def test_each_added_study_balances_condition_position(self):
        for blocks in ORDERS.values():
            self.assertEqual(len(blocks), 3)
            for block in blocks:
                self.assertEqual(set(block), set(CONDITIONS))
            for slot in range(3):
                self.assertEqual({block[slot] for block in blocks}, set(CONDITIONS))


if __name__ == "__main__":
    unittest.main()
