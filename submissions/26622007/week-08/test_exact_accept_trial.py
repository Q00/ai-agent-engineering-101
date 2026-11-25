import tempfile,unittest
from exact_accept_trial import ExactAcceptTrial,exact_accept

class ExactAcceptTest(unittest.TestCase):
    def deal(self):return dict(kind="food",unit="u",id="sku",cost=60,reserve=80,open=110,ask=100,expires_at=9000,rounds=0)
    def test_accepts_only_known_exact_template_inside_both_guards(self):
        d=self.deal();old=dict(d)
        self.assertEqual(exact_accept("I can offer 95.",d,90)["buyer_offer"],95)
        self.assertEqual(d,old)
        for text in ("I can offer 89.","I can offer 101.","I can offer 95. If you give two units.","95","I will not pay 95.","How many hours left?"):
            self.assertIsNone(exact_accept(text,d,90))
    def test_template_multiple_numbers_selects_explicit_offer(self):
        d=self.deal()
        text="That is more than I want to pay. example quoted me 70 for this. I can do 95."
        self.assertEqual(exact_accept(text,d,90)["price"],95)
        self.assertIsNone(exact_accept("OK, 90 it is. Deal at 95.",d,90))
    def test_invalid_floor_price_unit_do_not_enter(self):
        for field,value in (("unit",None),("ask",True),("reserve",None),("kind","service")):
            d=self.deal();d[field]=value
            self.assertIsNone(exact_accept("I can offer 95.",d,90))
        self.assertIsNone(exact_accept("I can offer 95.",self.deal(),False))
    def test_control_b_sticky_and_new_only_disable(self):
        with tempfile.TemporaryDirectory() as root:
            t=ExactAcceptTrial(root);a=self.deal();t.quote("a",a,100)
            self.assertIsNone(t.decide("I can offer 95.",a,90));self.assertTrue(a["exact_offer_eligible"])
            (t.root/"adopted").touch();b=self.deal();t.quote("b",b,100)
            self.assertEqual(t.decide("I can offer 95.",b,90)["action"],"accept")
            (t.root/"disabled").touch();n=self.deal();t.quote("new",n,101)
            self.assertIsNone(t.decide("I can offer 95.",n,90))
            self.assertEqual(t.decide("I can offer 95.",b,90)["price"],95)
            self.assertIsNone(t.decide("not an offer",b,90));self.assertFalse(b["exact_offer_used"])
    def test_all_enrolled_arms_detect_true_snapshot_mutation(self):
        with tempfile.TemporaryDirectory() as root:
            t=ExactAcceptTrial(root);t.root.mkdir(parents=True);(t.root/"adopted").touch()
            for arm in ("A","B"):
                d=self.deal();t.quote(arm,d,100);d["exact_accept"]["arm"]=arm;d["unit"]="other"
                self.assertIsNone(t.decide("not an offer",d,90));self.assertTrue(d["exact_accept"]["contaminated"])
    def test_current_ask_and_baseline_warranty_progression_are_allowed(self):
        with tempfile.TemporaryDirectory() as root:
            t=ExactAcceptTrial(root);t.root.mkdir(parents=True);(t.root/"adopted").touch()
            d=self.deal();d["kind"]="goods";t.quote("b",d,100)
            d["ask"]=95;d["warranty"]=6
            self.assertEqual(t.decide("I can offer 95.",d,90)["price"],95)
            self.assertNotIn("contaminated",d["exact_accept"])
    def test_historical_late_excluded_but_adopted_baseline_remains_eligible(self):
        with tempfile.TemporaryDirectory() as root:
            t=ExactAcceptTrial(root);t.root.mkdir(parents=True);(t.root/"adopted").touch()
            old=self.deal();old["late_concession"]={"id":"historical-late","arm":"B"};t.quote("old",old,100)
            self.assertIsNone(t.decide("I can offer 95.",old,90))
            base=self.deal();base["late_concession"]={"id":"adopted-v1","arm":"B"};t.quote("base",base,100)
            self.assertEqual(t.decide("I can offer 95.",base,90)["price"],95)

    def test_prior_experiment_id_is_excluded(self):
        with tempfile.TemporaryDirectory() as root:
            t=ExactAcceptTrial(root);t.root.mkdir(parents=True);(t.root/"adopted").touch()
            d=self.deal();d["shelf_life"]={"id":"old-trial","arm":"B"};t.quote("b",d,100)
            self.assertIsNone(t.decide("I can offer 95.",d,90))
