"""Public exact-counter boundary; real rule formula, no network."""
import unittest,tempfile,json,pathlib
from exact_counter_trial import ExactCounterTrial
from unittest.mock import patch
from exact_counter_trial import exact_counter

class ExactCounterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        path=pathlib.Path(self.tmp.name)/"pricing.json";path.write_text("{}")
        self.fs=patch("shop_memory.PRICING",str(path));self.fs.start();self.addCleanup(self.fs.stop)
        self.env=patch.dict("os.environ",{"NEGOTIATE_ENGINE":"jevrule"});self.env.start();self.addCleanup(self.env.stop)
    def test_explicit_midbid_uses_existing_counter_formula(self):
        deal=dict(kind="food",unit="unit",ask=100,reserve=80,open=110,rounds=1,history=[])
        with patch.dict("os.environ",{"NEGOTIATE_ENGINE":"jevrule"}):
            got=exact_counter("I can offer 85.",deal,90)
        self.assertEqual((got["action"],got["buyer_offer"],got["price"]),("counter",85,96))

    def deal(self):
        return dict(kind="food",unit="u",id="sku",cost=60,reserve=80,open=110,ask=100,expires_at=9000,rounds=0)
    def test_nonoffers_accepts_lowballs_final_round_and_engine_off_stay_baseline(self):
        d=dict(self.deal(),rounds=1);old=dict(d)
        for text in ("85","I can offer 90.","I can offer 101.","I can offer 49.","I can offer 85. But send two units.","Do not accept 85.","OK, 85 it is. Deal at 86."):
            self.assertIsNone(exact_counter(text,d,90),text)
        self.assertIsNone(exact_counter("I can offer 85.",dict(d,rounds=10),90))
        self.assertIsNone(exact_counter("I can offer 85.",dict(d,ask=90),90))
        with patch.dict("os.environ",{"NEGOTIATE_ENGINE":"llm"}):
            self.assertIsNone(exact_counter("I can offer 85.",d,90))
        self.assertEqual(d,old)
    def test_round_dependent_formula_uses_existing_configuration(self):
        cfg=pathlib.Path(self.tmp.name)/"pricing.json"
        cfg.write_text(json.dumps({"negotiate":{"counter_share":[0.8,0.5]}}))
        self.assertEqual(exact_counter("I can offer 85.",dict(self.deal(),rounds=1),90)["price"],98)
        self.assertEqual(exact_counter("I can offer 85.",dict(self.deal(),rounds=2),90)["price"],95)
    def test_new_only_disable_sticky_and_adopted_baselines_coexist(self):
        t=ExactCounterTrial(self.tmp.name);a=self.deal();t.quote("a",a,100);a["rounds"]=1
        self.assertIsNone(t.decide("I can offer 85.",a,90));self.assertTrue(a["exact_counter_eligible"])
        (t.root/"adopted").touch()
        b=self.deal();b["exact_accept"]={"id":"adopted-v1","arm":"B"};b["late_concession"]={"id":"adopted-v1","arm":"B"}
        t.quote("b",b,100);b["rounds"]=1
        self.assertEqual(t.decide("I can offer 85.",b,90)["price"],96)
        (t.root/"disabled").touch();c=self.deal();t.quote("c",c,101);c["rounds"]=1
        self.assertIsNone(t.decide("I can offer 85.",c,90))
        self.assertEqual(t.decide("I can offer 85.",b,90)["price"],96)
        self.assertIsNone(t.decide("I can offer 95.",b,90));self.assertFalse(b["exact_counter_used"])
    def test_historical_trials_excluded_both_arms_detect_real_mutation(self):
        t=ExactCounterTrial(self.tmp.name);t.root.mkdir(parents=True);(t.root/"adopted").touch()
        for key in ("reader_trial","concession_trial","opening_cue","late_speed","jev_transport","shelf_life","late_concession","exact_accept"):
            d=self.deal();d[key]={"id":"historical","arm":"B"};t.quote(key,d,100);d["rounds"]=1
            self.assertIsNone(t.decide("I can offer 85.",d,90),key)
        for arm in ("A","B"):
            d=self.deal();t.quote("mutate"+arm,d,100);d["exact_counter"]["arm"]=arm;d["unit"]="different";d["rounds"]=1
            self.assertIsNone(t.decide("I can offer 85.",d,90))
            self.assertTrue(d["exact_counter"]["contaminated"])
    def test_current_ask_and_baseline_warranty_terms_are_not_false_contamination(self):
        t=ExactCounterTrial(self.tmp.name);t.root.mkdir(parents=True);(t.root/"adopted").touch()
        d=self.deal();t.quote("current",d,100);d.update(ask=96,warranty=72,rounds=2)
        self.assertEqual(t.decide("I can offer 85.",d,90)["price"],93)
        self.assertNotIn("contaminated",d["exact_counter"])
