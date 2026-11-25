"""Public pricing seam: current policy is control; candidate never crosses reserve."""
import tempfile
import unittest
from pathlib import Path
from seller_late_speed import LateSpeed

class LateSpeedTest(unittest.TestCase):
    def deal(self):
        return dict(unit="u-test",id="sku",kind="goods",cost=70,reserve=100,open=200,ask=190,warranty=6,rounds=0,reader_trial_policy_sha="fixed",late_concession=dict(id="adopted-v1",arm="B"))
    def test_new_adopted_candidate_only_changes_late_floor(self):
        with tempfile.TemporaryDirectory() as root:
            t=LateSpeed(root);t.root.mkdir(parents=True);(t.root/"adopted").touch()
            d=self.deal();before={k:d[k] for k in ("ask","open","reserve","warranty","cost","unit")}
            t.quote("full-task",d,1000)
            self.assertEqual(t.floor(d,145,150),145)
            d["rounds"]=3
            self.assertEqual(t.floor(d,145,150),140)
            self.assertEqual({k:d[k] for k in before},before)
    def test_control_and_reserve_guard(self):
        with tempfile.TemporaryDirectory() as root:
            t=LateSpeed(root);d=self.deal();t.quote("control",d,1000);d["rounds"]=3
            self.assertEqual(t.floor(d,145,150),145)
            (t.root/"adopted").touch();t.quote("control",d,1001)
            self.assertEqual(t.floor(d,145,150),145)
            e=self.deal();t.quote("new",e,1001);e["rounds"]=3
            self.assertEqual(t.floor(e,100,100),100)

if __name__=="__main__":unittest.main()
