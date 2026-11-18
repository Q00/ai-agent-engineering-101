import json
import tempfile
import time
import unittest
from pathlib import Path
from patch36 import expected_margin_share,margin_share,cost_estimate


class ExpectedMarginDistributionTests(unittest.TestCase):
    def fixture(self,md,samples):
        Path(md,'patch36-market.json').write_text(json.dumps(dict(at=time.time(),costs={'x:A':dict(n=2,ratio=sum(samples)/len(samples),samples=samples)},cost_priors={'goods:A':dict(n=20,ratio=sum(samples)/len(samples),samples=samples)},cost_kinds={'x':'goods'})))

    def test_score_cap_is_applied_before_averaging(self):
        with tempfile.TemporaryDirectory() as d:
            self.fixture(d,[.4,.6])
            self.assertAlmostEqual(expected_margin_share(100,50,'x','A',d),(.5/.6+1)/2)
            self.assertNotEqual(expected_margin_share(100,50,'x','A',d),margin_share(100,50,50))

    def test_below_both_costs_keeps_full_score(self):
        with tempfile.TemporaryDirectory() as d:
            self.fixture(d,[.4,.6]);self.assertEqual(expected_margin_share(100,30,'x','A',d),1)

    def test_observed_cost_above_list_is_not_clipped_to_fake_low_cost(self):
        with tempfile.TemporaryDirectory() as d:
            self.fixture(d,[1.1,1.2]);self.assertEqual(expected_margin_share(100,100,'x','A',d),1)

    def test_old_snapshot_remains_usable(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d,'patch36-market.json').write_text(json.dumps(dict(at=time.time(),costs={'x:A':dict(n=2,ratio=.5)})))
            self.assertEqual(expected_margin_share(100,80,'x','A',d),margin_share(100,80,cost_estimate(100,'x','A',d)))
