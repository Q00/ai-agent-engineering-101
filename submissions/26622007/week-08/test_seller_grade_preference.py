"""Public StockBook/Seller preference fallback and pre-pick enrollment seams."""
import unittest,tempfile,pathlib,json
from shop_stock import StockBook

BC="Grade B or C is fine if it is cheaper; please quote your lowest-priced unit."
class GradePreferenceTests(unittest.TestCase):
    def unit(self,unit="uA",grade="A"):
        return dict(unit=unit,sku="G12345678",name="box",category="goods",kind="goods",grade=grade,cost=100,cert=True,supplied_at=10)
    def test_only_grade_failure_can_recover_higher_grade_stock(self):
        stock=StockBook(load=lambda:dict(units=[self.unit()]))
        text="G12345678. "+BC
        self.assertIsNone(stock.pick(text)[0])
        got,miss=stock.pick(text,allow_grade_preference=True)
        self.assertEqual(got["unit"],"uA");self.assertIsNone(miss)

    def test_adopted_new_requests_survive_end_while_disable_is_new_only(self):
        import uuid
        from seller_grade_preference import GradePreferenceTrial
        with tempfile.TemporaryDirectory() as root:
            t=GradePreferenceTrial(root);t.root.mkdir(parents=True);(t.root/"adopted").touch()
            task=str(uuid.uuid4());a=t.enroll(task,"G12345678. "+BC,100)
            self.assertEqual((a["id"],a["arm"]),("adopted-v1","B"))
            (t.root/"disabled").touch()
            self.assertEqual(t.enroll(task,"G12345678. "+BC,101)["arm"],"B")
            self.assertEqual(t.enroll(str(uuid.uuid4()),"G12345678. "+BC,101)["arm"],"A")

    def test_existing_success_keeps_original_unit_even_if_fallback_is_cheaper(self):
        a=self.unit();b=self.unit("uB","B");a["cost"]=1
        stock=StockBook(load=lambda:dict(units=[a,b]))
        text="G12345678. "+BC
        normal=stock.pick(text,rank=lambda u:u["cost"])[0]
        selection={};after=stock.pick(text,rank=lambda u:u["cost"],allow_grade_preference=True,selection=selection)[0]
        self.assertEqual((normal["unit"],after["unit"]),("uB","uB"))
        self.assertFalse(selection.get("preference_used"))
    def test_independent_grade_policy_or_unknown_qualifier_remains_strict(self):
        stock=StockBook(load=lambda:dict(units=[self.unit()]))
        for suffix in (' Required checkout policy: {"grades":["B"],"cert":true}.',
                       " Only grade C."," Only C."," only c."," Grade Q is required."," B-grade only."):
            self.assertIsNone(stock.pick("G12345678. "+BC+suffix,allow_grade_preference=True)[0],suffix)
    def test_non_grade_fit_and_holds_are_not_weakened(self):
        for change,text in (({"cert":False},"G12345678. certificate required. "+BC),
                            ({},"G87654321. "+BC),
                            ({"kind":"food","expires_at":1000},"G12345678 needs shelf life 1 hours. "+BC),
                            ({"grade":"expired"},"G12345678. "+BC),
                            ({"grade":"Q"},"G12345678. "+BC)):
            u=self.unit();u.update(change);stock=StockBook(load=lambda:dict(units=[u]))
            self.assertIsNone(stock.pick(text,now=100,allow_grade_preference=True)[0])
        stock=StockBook(load=lambda:dict(units=[self.unit()]))
        stock.hold("uA","another-task")
        self.assertIsNone(stock.pick("G12345678. "+BC,allow_grade_preference=True)[0])
    def test_json_string_sentence_does_not_create_a_new_policy(self):
        from seller_grade_preference import preference_needs
        self.assertIsNone(preference_needs(json.dumps({"comment":BC})))
        self.assertIsNone(preference_needs("G12345678. "+BC+" "+BC))
    def test_pre_pick_assignments_are_bounded_sticky_and_keep_rejections(self):
        import uuid
        from seller_grade_preference import GradePreferenceTrial
        with tempfile.TemporaryDirectory() as root:
            t=GradePreferenceTrial(root);t.root.mkdir(parents=True)
            cfg=dict(enabled=True,id="grade-test",start=100,end=1900,seed=7,stop=False)
            (t.root/"seller_concession_trial.json").write_text(json.dumps(cfg))
            aa=[(str(uuid.uuid4()),None) for _ in range(210)]
            aa=[(task,t.enroll(task,"G12345678. "+BC,101)) for task,_ in aa]
            included=[(task,a) for task,a in aa if a.get("id")]
            self.assertEqual(len(included),200)
            self.assertEqual(sum(a["arm"]=="B" for _,a in included),100)
            task,a=included[0]
            t.selected(task,a,None,"grade A, B/C asked",{})
            self.assertEqual(t.enroll(task,"G12345678. "+BC,2000)["arm"],a["arm"])
            changed=t.enroll(task,"G12345678. changed request",2000)
            self.assertTrue(changed["contaminated"]);self.assertEqual(t.arm(changed),"A")
            self.assertIsNone(t.enroll("bad-prefix","G12345678. "+BC,101)["id"])
            self.assertEqual(sum(a.get("id") is not None for _,a in included),200)

    def test_json_string_braces_and_quoted_structured_context_do_not_relax(self):
        from seller_grade_preference import preference_needs
        for text in ("G12345678 "+json.dumps({"note":BC+"{"}),
                     "G12345678 "+json.dumps({"note":{"text":BC+"}"}}),
                     "G12345678 "+json.dumps([BC]),
                     json.dumps(BC),
                     "G12345678 {"+BC):
            self.assertIsNone(preference_needs(text),text[:12])
