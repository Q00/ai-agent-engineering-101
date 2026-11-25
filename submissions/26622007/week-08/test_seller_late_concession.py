import json, tempfile, unittest
from pathlib import Path
from seller_late_concession import LateConcession

class LateFloorTests(unittest.TestCase):
    def test_only_round_three_onwards_reduces_remaining_margin(self):
        with tempfile.TemporaryDirectory() as root:
            p = LateConcession(root)
            d = dict(rounds=2, reserve=100, ask=180, late_concession=dict(arm="B", id="adopted-v1"))
            self.assertEqual(p.floor(d, 160),160)
            d["rounds"]=3
            self.assertEqual(p.floor(d,160),154)
            self.assertEqual(p.floor(d,100),100)
            d["ask"]=120
            self.assertEqual(p.floor(d,120),118)
    def test_control_and_missing_assignment_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            p=LateConcession(root)
            for a in ({},dict(arm="A",id="adopted-v1")):
                d=dict(rounds=5,reserve=100,ask=180,late_concession=a)
                self.assertEqual(p.floor(d,160),160)
    def test_disabled_stops_new_treatment_even_with_active_trial(self):
        with tempfile.TemporaryDirectory() as root:
            p=LateConcession(root);p.root.mkdir()
            (p.root/"seller_concession_trial.json").write_text(json.dumps(dict(enabled=True,id="new-trial",seed=1,start=100,end=1900)))
            (p.root/"disabled").touch()
            for i in range(4):
                d=dict(rounds=0,reserve=100,open=200,ask=194,cost=90,unit=str(i),id="sku",kind="goods",history=[("you asked",194)])
                p.quote(str(i),d,now=101)
                self.assertEqual(d["ask"],194)
                d["rounds"]=3
                self.assertEqual(p.floor(d,160),160)
    def test_uuid_sticky_across_stop_adoption_and_new_control(self):
        with tempfile.TemporaryDirectory() as root:
            p=LateConcession(root);p.root.mkdir()
            def deal():return dict(rounds=0,reserve=100,open=200,ask=194,cost=90,unit="u",id="sku",kind="goods",history=[("you asked",194)])
            a=deal();p.quote("control",a,100)
            (p.root/"adopted").touch()
            b=deal();p.quote("control",b,101)
            self.assertEqual(b["late_concession"],a["late_concession"])
            c=deal();p.quote("new",c,102)
            self.assertEqual(c["late_concession"]["arm"],"B")
            (p.root/"adopted").unlink()
            (p.root/"seller_concession_trial.json").write_text(json.dumps(dict(stop=True)))
            d=deal();p.quote("new",d,103)
            self.assertEqual(d["late_concession"],c["late_concession"])
            p.event("new",d,"deal",154,104)
            trace=[json.loads(x) for x in (p.root/"seller_concession_trial.jsonl").read_text().splitlines()]
            self.assertEqual(trace[-1]["event"],"deal")
            self.assertEqual(trace[-1]["ts"],104)
            self.assertEqual(trace[-1]["price"],154)
if __name__=="__main__":unittest.main()


