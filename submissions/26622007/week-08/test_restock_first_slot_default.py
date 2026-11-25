"""Normal adopted policy: public main, expired finite reader, separate control."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import test_restock_first_slot as fixture
import restock_first_slot as slot

ON={"enabled":True,"policy":"first-slot-default-v1","sku":"G022ce883"}
class FirstSlotDefaultTest(unittest.TestCase):
    run_main=fixture.FirstSlotMainTest.run_main
    def test_adopted_default_matches_finite_B_without_reopening_expired_trial(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            finite,state=self.run_main(root,preopen=True)
            out,state=self.run_main(root,arm="A",expired=True,preopen=True,default_control=ON)
            self.assertEqual(out,finite)
            self.assertEqual(out["buy"],[{"sku":"G022ce883","qty":1}])
            self.assertEqual(json.loads((state/slot.SWITCH).read_text())["stop"],120000)
            rows=[json.loads(x) for x in (state/"measure/restock_first_slot_default.jsonl").read_text().splitlines()]
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]["study"],"first-slot-default-v1")
            self.assertEqual(rows[0]["arm"],"B")
            self.assertEqual(rows[0]["sku"],"G022ce883")
    def test_absent_off_malformed_and_other_sku_keep_baseline(self):
        for control in (None,{**ON,"enabled":False},"{",[],{**ON,"sku":"other"},{**ON,"extra":1}):
            with self.subTest(control=control),tempfile.TemporaryDirectory() as td:
                out,state=self.run_main(Path(td),arm="OFF",expired=True,default_control=control)
                self.assertEqual(out["buy"],[])
                self.assertFalse((state/"measure/restock_first_slot_default.jsonl").exists())
    def test_default_keeps_all_original_guards_and_fast_injection(self):
        for kw in ({"held":1},{"onhand":True},{"old_active":True},{"balance":0},{"bad_pending":True},{"late":True},{"fast":True,"injected":True},{"reserved":True},{"preopen":True,"reserved":True,"sent_trial":True},{"open_flag":None},{"overrides":{"food_max_stock_cost":0}},{"overrides":{"skip_skus":["G022ce883"]}}):
            with self.subTest(kw=kw),tempfile.TemporaryDirectory() as td:
                out,state=self.run_main(Path(td),arm="OFF",default_control=ON,**kw)
                self.assertFalse(any(b["sku"]=="G022ce883" for b in out["buy"]))
    def test_default_late_provenance_excludes_only_new_target(self):
        for lines,cap,target in [([{"sku":"other1","qty":1},{"sku":"G022ce883","qty":3}],75,0),([{"sku":"G022ce883","qty":3}],100000,1)]:
            with self.subTest(lines=lines),tempfile.TemporaryDirectory() as td:
                out,state=self.run_main(Path(td),arm="OFF",default_control=ON,late=True,agent_lines=lines,overrides={"food_max_stock_cost":cap})
                self.assertEqual(out["buy"],[])
                q=json.loads((state/"restock_queue.json").read_text())["lines"]
                self.assertEqual(sum(b["qty"] for b in q if b["sku"]=="G022ce883"),target)
    def test_observation_failure_never_changes_default_proposal(self):
        with tempfile.TemporaryDirectory() as td:
            out,state=self.run_main(Path(td),arm="OFF",default_control=ON,fail_write=True)
            self.assertEqual(out["buy"],[{"sku":"G022ce883","qty":1}])

    def test_default_reader_preserves_eligibility_and_window_contract(self):
        inv={"restock":{"open":False,"opens_at":120000,"closes_at":120180}}
        model={"active":True,"skus":{"G022ce883":{"mine_rate":0,"deals":3}}}
        catalog={"G022ce883":{"kind":"food","shelf_hours":48}}
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/"restock_first_slot_default.json").write_text(json.dumps(ON))
            self.assertIsNone(slot.config(root,inv,model,catalog,[],now=119960))
            self.assertEqual(slot.default_config(root,inv,model,catalog,[],now=119960)["arm"],"B")
            for changed in ({"active":False,"skus":model["skus"]},{"active":True,"skus":{"G022ce883":{"mine_rate":1,"deals":3}}},{"active":True,"skus":{"G022ce883":{"mine_rate":0,"deals":2}}}):
                self.assertIsNone(slot.default_config(root,inv,changed,catalog,[],now=119960))
            self.assertIsNone(slot.default_config(root,inv,model,catalog,["G022ce883"],now=119960))
            self.assertIsNone(slot.default_config(root,inv,model,{"G022ce883":{"kind":"food","shelf_hours":24}},[],now=119960))
            for now in (119949,120180):self.assertIsNone(slot.default_config(root,inv,model,catalog,[],now=now))
    def test_finite_A_has_priority_over_separate_default_control(self):
        with tempfile.TemporaryDirectory() as td:
            out,state=self.run_main(Path(td),arm="A",default_control=ON)
            self.assertEqual(out["buy"],[])
            self.assertFalse((state/"measure/restock_first_slot_default.jsonl").exists())
    def test_default_preopen_reaches_native_kit_only_after_open(self):
        import time,market_kit as kit
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);out,state=self.run_main(root,arm="OFF",preopen=True,default_control=ON)
            inv=json.loads((state/"inventory.json").read_text());clock=[119965.0];calls=[]
            class Reply:
                status_code=200
                text="offline"
                def json(self):return {**inv,"balance":10**9,"total":75}
            class Market:
                def call(self,method,path,role,**kwargs):
                    if method=="POST":calls.append((clock[0],kwargs["json"]))
                    return Reply()
            def sleep(seconds):clock[0]+=seconds
            with patch.object(time,"time",side_effect=lambda:clock[0]),patch.object(time,"monotonic",side_effect=lambda:clock[0]),patch.object(time,"sleep",side_effect=sleep),patch.object(kit,"run_buyer",return_value=[json.dumps(out)]),patch.object(kit,"_supply_last",[None]),patch.object(kit,"log",return_value=None):
                kit.restock({"restock":{"cmd":"offline"}},root,Market(),state,inv,[])
            self.assertEqual(len(calls),1);self.assertGreaterEqual(calls[0][0],120000)
            self.assertEqual(calls[0][1],{"sku":"G022ce883","qty":1})
