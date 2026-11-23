import unittest

from adaptive_control import stable_arm
from adaptive_stats import PCG64, cluster_bootstrap, decide, holm, percentile_type7, seed_v1


class ExperimentPolicyTests(unittest.TestCase):
    def setUp(self):
        self.t={f's{i}':[2+i/100,2.1+i/100] for i in range(5)}
        self.c={f's{i}':[1+i/100,1.1+i/100] for i in range(5)}

    def test_assignment_and_seed_are_stable(self):
        self.assertEqual(stable_arm('e','u'),stable_arm('e','u'))
        self.assertEqual(seed_v1('e','v'),seed_v1('e','v'))
        a=PCG64(1);b=PCG64(1);self.assertEqual([a.randbelow(9) for _ in range(30)],[b.randbelow(9) for _ in range(30)])

    def test_bootstrap_and_type7_are_deterministic(self):
        a=cluster_bootstrap('e','v',self.t,self.c,resamples=1000)
        b=cluster_bootstrap('e','v',self.t,self.c,resamples=1000)
        self.assertEqual(a,b);self.assertGreater(a['ci_low'],0)
        self.assertEqual(percentile_type7([0,10],.25),2.5)

    def test_holm_stops_after_first_failure(self):
        out=holm({'a':.001,'b':.02,'c':.021})
        self.assertTrue(out['a']);self.assertFalse(out['b']);self.assertFalse(out['c'])

    def test_minimum_and_double_sample_decisions(self):
        stats={'ci_low':.6,'ci_high':1,'p':.001}
        self.assertEqual(decide(stats,.5,True,'minimum'),'EXPANDING')
        self.assertEqual(decide(stats,.5,True,'2x'),'ADOPTED')
        self.assertEqual(decide(dict(stats,ci_low=.1),.5,True,'minimum'),'RUNNING')
        self.assertEqual(decide(dict(stats,ci_low=.1),.5,True,'2x'),'REJECTED')
        self.assertEqual(decide(stats,.5,False,'2x'),'REJECTED')


if __name__=='__main__':unittest.main()
