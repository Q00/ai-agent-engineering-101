import tempfile
import unittest

from safety_kernel import SafetyKernel


class SafetyKernelTests(unittest.TestCase):
    def test_three_consecutive_failures_block_only_new_mutations(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=SafetyKernel(tmp);c=s.control
            mutation,_=c.prepare_mutation('buyer','offer','before')
            c.move_mutation(mutation,'SUBMITTED','sent');c.move_mutation(mutation,'UNCERTAIN','lost')
            for _ in range(3):s.recovery_check(False,True,True,True,True)
            with self.assertRaises(PermissionError):c.prepare_mutation('buyer','offer','after')
            c.move_mutation(mutation,'RECONCILING','official_check')
            self.assertEqual(c.mutation_state(mutation),'RECONCILING');s.close()

    def test_success_resets_failures_before_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=SafetyKernel(tmp)
            s.recovery_check(False,True,True,True,True)
            s.recovery_check(True,True,True,True,True)
            self.assertEqual(s._get('consecutive_recovery_failures',99),0)
            self.assertTrue(s.allow(cash=100,liabilities=20,incremental_cost=30,inventory_slots=10)[0]);s.close()

    def test_one_official_bankruptcy_blocks_new_risk_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=SafetyKernel(tmp)
            self.assertFalse(s.record_bankruptcy('noise','forum_claim','x'))
            self.assertTrue(s.record_bankruptcy('b1','official_bankruptcy','hash'))
            self.assertFalse(s.record_bankruptcy('b1','official_bankruptcy','hash'))
            allowed,reasons=s.allow(cash=100,liabilities=0,incremental_cost=1,inventory_slots=1)
            self.assertFalse(allowed);self.assertIn('bankruptcy_limit',reasons);s.close()

    def test_hard_inventory_cash_and_defend_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=SafetyKernel(tmp)
            self.assertFalse(s.allow(cash=100,liabilities=90,incremental_cost=11,inventory_slots=1)[0])
            self.assertFalse(s.allow(cash=100,liabilities=0,incremental_cost=1,inventory_slots=251)[0])
            self.assertFalse(s.allow(cash=100,liabilities=0,incremental_cost=1,inventory_slots=1,
                defend=True,parameter_change=.101)[0]);s.close()


if __name__=='__main__':unittest.main()
