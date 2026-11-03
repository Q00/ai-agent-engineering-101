"""hard_limits.py: the lines the meta-harness cannot move. Protected like the module itself."""
import json
import os
import unittest

import hard_limits as hl

HERE = os.path.dirname(os.path.abspath(__file__))


class HardLimitsTest(unittest.TestCase):
    def pricing(self):
        with open(os.path.join(HERE, "pricing.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_the_shipped_settings_are_inside_the_bounds(self):
        self.assertEqual(hl.check_policy_change({}, self.pricing()), [])

    def test_never_spends_into_the_bankruptcy_zone(self):
        self.assertEqual(hl.spendable(900000), 0)
        self.assertEqual(hl.spendable(1500000), 500000)
        self.assertGreater(hl.STOP_BUYING_BELOW, hl.BANKRUPT_LINE)

    def test_one_bounded_step_per_cycle(self):
        old = self.pricing()
        new = json.loads(json.dumps(old))
        new["restock"]["max_stock_cost"] = old["restock"]["max_stock_cost"] * 1.4
        self.assertEqual(hl.check_policy_change(old, new), [])
        new["restock"]["max_stock_cost"] = old["restock"]["max_stock_cost"] * 2
        self.assertIn("more than 50%", hl.check_policy_change(old, new)[0])
        new = json.loads(json.dumps(old))
        new["restock"]["keep_cash"] = 900000
        self.assertTrue(any("outside" in p for p in hl.check_policy_change(old, new)))
        new = json.loads(json.dumps(old))
        new["restock"]["target_per_category"] = 2           # small integers may move by one
        self.assertEqual(hl.check_policy_change(old, new), [])
        del new["restock"]["max_break"]
        self.assertTrue(any("removed" in p for p in hl.check_policy_change(old, new)))
        new = json.loads(json.dumps(old))
        new["markdown"]["below_cost"] = ["food_expiry", "everything"]
        self.assertTrue(hl.check_policy_change(old, new))

    def test_protected_files_and_guard_lines(self):
        self.assertTrue(hl.guard_problems(["hard_limits.py"], ""))
        self.assertTrue(hl.guard_problems(["restock.py"], "-    spare = min(x, hard_limits.spendable(balance))\n"))
        self.assertEqual(hl.guard_problems(["restock.py"], "+    x = 1\n-    y = 2\n"), [])

    def test_the_sandbox_shuts_out_network_keys_and_writes(self):
        self.assertIn("(deny network*)", hl.SANDBOX)
        self.assertIn(".env", hl.SANDBOX)
        self.assertIn("(deny file-write*)", hl.SANDBOX)


if __name__ == "__main__":
    unittest.main()
