import unittest
from shelf_life_reply import factual_answer, ShelfLifeTrial
import tempfile,json

class ShelfLifeTest(unittest.TestCase):
    def deal(self):return dict(kind="food",unit="unit",expires_at=8200,ask=12345,reserve=11000)
    def test_truthful_answer_keeps_price_and_terms(self):
        d=self.deal();before=dict(d)
        r=factual_answer("How many hours of shelf life does this unit have left? It must last at least 1 hours.",d,1000)
        self.assertEqual(r["hours"],2)
        self.assertIn("2.00 hours",r["message"])
        self.assertTrue(r["message"].endswith("My price: 12345."))
        self.assertEqual(d,before)
    def test_no_false_warranty_or_unmet_life(self):
        text="How many hours of shelf life does this unit have left? It must last at least 3 hours."
        self.assertIsNone(factual_answer(text,self.deal(),1000))
        d=self.deal();d["kind"]="goods"
        self.assertIsNone(factual_answer(text,d,1000))
    def test_ambiguous_or_offer_input_remains_baseline(self):
        for text in ["I offer 10000.","How many hours left?","How many hours of shelf life does this unit have left? It must last at least 1 hours. I offer 10000."]:
            self.assertIsNone(factual_answer(text,self.deal(),1000))
    def test_invalid_or_expired_unit_stays_baseline(self):
        text="How many hours of shelf life does this unit have left? It must last at least 1 hours."
        for expiry in (None,False,float("nan"),float("inf"),900):
            d=self.deal();d["expires_at"]=expiry
            self.assertIsNone(factual_answer(text,d,1000))

class ShelfTrialTest(unittest.TestCase):
    def deal(self):return dict(kind="food",unit="unit",id="sku",cost=5000,reserve=11000,open=13000,ask=12345,expires_at=8200,rounds=0)
    def test_only_b_answers_without_changing_price(self):
        text="How many hours of shelf life does this unit have left? It must last at least 1 hours."
        with tempfile.TemporaryDirectory() as root:
            t=ShelfLifeTrial(root);a=self.deal();t.quote("a",a,1000)
            (t.root/"adopted").touch();b=self.deal();t.quote("b",b,1001)
            self.assertIsNone(t.reply(text,a,1002));self.assertTrue(a["shelf_eligible"])
            old=b["ask"];self.assertIn("hours of shelf life",t.reply(text,b,1002));self.assertEqual(b["ask"],old)
            t.event("b",b,"turn",old,1002)
            e=json.loads((t.root/"seller_concession_trial.jsonl").read_text().splitlines()[-1]);self.assertTrue(e["shelf_reply_used"]);self.assertEqual(e["id"],"adopted-v1")
            self.assertIsNone(t.reply("I offer 12000.",b,1003));self.assertFalse(b["shelf_reply_used"]);self.assertFalse(b["shelf_eligible"])
    def test_all_enrolled_arms_detect_mutation_even_when_answer_ineligible(self):
        for arm in ("A","B"):
            for message in ("I offer 12000.", "How many hours of shelf life does this unit have left? It must last at least 1 hours."):
                with self.subTest(arm=arm,message=message),tempfile.TemporaryDirectory() as root:
                    t=ShelfLifeTrial(root);t.root.mkdir(parents=True);(t.root/"adopted").touch()
                    d=self.deal();t.quote("registered",d,1000)
                    d["shelf_life"]["arm"]=arm
                    d["expires_at"]=900
                    self.assertIsNone(t.reply(message,d,1001))
                    self.assertTrue(d["shelf_life"].get("contaminated"))
                    self.assertEqual(d["shelf_life"]["arm"],arm)
                    self.assertFalse(d["shelf_reply_used"])

    def test_expiry_snapshot_mutation_and_new_only_disable(self):
        with tempfile.TemporaryDirectory() as root:
            t=ShelfLifeTrial(root);t.root.mkdir(parents=True);(t.root/"adopted").touch();d=self.deal();t.quote("b",d,1000)
            d["expires_at"]+=3600
            self.assertIsNone(t.reply("How many hours of shelf life does this unit have left? It must last at least 1 hours.",d,1001));self.assertTrue(d["shelf_life"]["contaminated"])
            (t.root/"disabled").touch();n=self.deal();t.quote("new",n,1002);self.assertEqual(n["shelf_life"]["arm"],"A")
