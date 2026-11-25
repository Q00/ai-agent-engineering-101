"""Public main proposal boundary for a separately finite first-slot strategy.

Synthetic system inputs isolate safety cases; actual cached full-main replay is
separately preserved in the review evidence. No live calls are permitted.
"""
import copy,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

# This module must find the native kit when run alone, before any sibling test.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import restock
import restock_first_stock as trial
import restock_first_slot as slot

class FirstSlotMainTest(unittest.TestCase):
    def run_main(self, root, arm="B", held=0, fail_write=False, injected=False, fast=False, balance=10**9, bad_pending=False, late=False, expired=False, overrides=None, onhand=False, agent_lines=None, stock=0, old_active=False, reserved=False, preopen=False, open_flag=True, sent_trial=False, old_ineligible=False, default_control=None):
        import contextlib, io, os, sys, time
        state = root / "market"
        state.mkdir(exist_ok=True)
        sku = "G022ce883" if old_ineligible else slot.SKU
        now = 119960 if preopen else 120179 if late else 120001
        inv = {"round": 3, "balance": balance, "units": [],
               "restock": {"open": False if preopen else open_flag, "opens_at": 120000, "closes_at": 120180}}
        if stock: inv["units"] = [{"sku":"held_other","kind":"goods","cost":stock}]
        if onhand: inv["units"] = [{"sku":sku,"kind":"food","cost":75}]
        catalog = {sku: {"kind": "food", "list_price": 100, "shelf_hours": 48}}
        for x in agent_lines or []:catalog[x["sku"]]={"kind":"food","list_price":100,"shelf_hours":48}
        if reserved:catalog["reserved"]={"kind":"food","list_price":100,"shelf_hours":48}
        (state / "inventory.json").write_text(json.dumps(inv))
        (state / "catalog.json").write_text(json.dumps(catalog))
        (state / "demand.json").write_text("[]")
        pricing = {"restock": {"first_stock": {"arm": "B"}, "first_slot": {"arm": "B"}}} if injected else {}
        if reserved:pricing["next_window"]={"id":"reserved-fixture","targets":{"reserved":1},"expires_hours":2}
        if sent_trial:
            (state/"next_window_state.json").write_text(json.dumps({"id":"reserved-fixture", "created":119900,
                "sent":[{"at":119900,"lines":[{"sku":"reserved","qty":1}]}],"done":False,"notes":[],"tries":{}}))
        pricing.setdefault("restock",{}).update(overrides or {})
        (root / "pricing.json").write_text(json.dumps(pricing))
        switch = state / slot.SWITCH
        switch.write_text(json.dumps({"enabled": True, "study": "record-test", "start": 119950,
                                      "stop": 120000 if expired else 123550, "b_parity": 0 if arm == "B" else 1}))
        if default_control is not None:
            (state/"restock_first_slot_default.json").write_text(default_control if isinstance(default_control,str) else json.dumps(default_control))
        if old_active:
            (state/trial.SWITCH).write_text(switch.read_text())
        if arm == "OFF":switch.unlink()
        if old_active or old_ineligible:
            catalog[trial.SKU] = {"kind":"food","list_price":100,"shelf_hours":48}
            (state / "catalog.json").write_text(json.dumps(catalog))
        model = {"active": True, "skus": {sku: {"mine_rate": 0, "deals": 20}}}
        if old_active:
            model["skus"][trial.SKU] = {"mine_rate":0,"deals":20}
        if old_ineligible:
            model["skus"][trial.SKU] = {"mine_rate":3,"deals":17}
            inv["units"] = [{"sku":trial.SKU,"kind":"food","cost":75} for _ in range(5)]
            (state / "inventory.json").write_text(json.dumps(inv))
            (state / trial.SWITCH).write_text(json.dumps({"enabled":True,"study":"preserved-old",
                "start":118200,"stop":120000,"b_parity":0}))
        waiting = [{"sku": sku, "qty": held, "worst": 75}] if held else []
        original_guard = restock.guard
        def plan(inv, catalog, demand, cfg, *args, **kwargs):
            # Repeated evaluation must not sum the same reduction twice.
            original_guard([{"sku": sku, "qty": 3}], inv, catalog, cfg, waiting=waiting)
            return {"buy": []}
        def fast_path(state, inv, pricing, cfg, catalog, note=None):
            self.assertIsNone(cfg.get("first_slot"))
            self.assertIsNone(cfg.get("first_stock"), "pricing cannot inject a trial into the fast guard")
            return {"buy": []}
        oldcwd = os.getcwd()
        output = io.StringIO()
        try:
            os.chdir(root)
            with contextlib.ExitStack() as st:
                st.enter_context(patch.dict(os.environ, HARNESS="offline" if agent_lines is not None else ""))
                st.enter_context(patch.object(time, "time", return_value=now))
                st.enter_context(patch.object(sys, "argv", ["restock.py", "--state", str(state)]))
                st.enter_context(patch.object(restock, "read_model", return_value=model))
                st.enter_context(patch.object(restock, "read_breaks", return_value=None))
                st.enter_context(patch.object(restock, "pending", side_effect=ValueError("synthetic unknown pending"))) if bad_pending else st.enter_context(patch.object(restock, "pending", return_value=waiting))
                st.enter_context(patch.object(restock, "warranted_sales", return_value=[]))
                st.enter_context(patch.object(restock, "plan", side_effect=plan))
                st.enter_context(patch.object(restock, "log_prices", return_value=None))
                st.enter_context(patch.object(restock.loss_watch, "check", return_value=None))
                st.enter_context(patch.object(restock.agent_memory, "maybe_consolidate", return_value=None))
                st.enter_context(patch.object(restock.shop_forum, "maybe_run", return_value=None))
                st.enter_context(patch.object(restock.measure, "tick", return_value=None))
                if agent_lines is not None:
                    st.enter_context(patch.object(restock,"agent_turn",return_value=agent_lines))
                    for obj,field in [(restock,'forum_off_window'),(restock.tool_forge,'maybe_run'),(restock.rival_vote,'maybe_run')]:st.enter_context(patch.object(obj,field,return_value=None))
                if fast: st.enter_context(patch.object(restock, "window_fast", side_effect=fast_path))
                if fail_write: st.enter_context(patch.object(slot.os, "fchmod", side_effect=OSError("denied")))
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(restock.main(), 0)
            return json.loads(output.getvalue().splitlines()[-1]), state
        finally:
            os.chdir(oldcwd)

    def test_B_reaches_first_slot_while_A_and_expiry_keep_baseline(self):
        for arm,expired,qty in [("A",False,0),("B",False,1),("B",True,0)]:
            with self.subTest(arm=arm,expired=expired), tempfile.TemporaryDirectory() as td:
                out,state=self.run_main(Path(td),arm=arm,expired=expired)
                self.assertEqual(sum(x["qty"] for x in out["buy"] if x["sku"]==slot.SKU),qty)
                if qty:self.assertEqual(out["buy"][0],{"sku":slot.SKU,"qty":1})

    def test_pending_unknown_cash_and_late_window_cannot_be_bypassed(self):
        for kwargs in ({"old_active":True},{"held":1},{"onhand":True},{"bad_pending":True},{"balance":0},{"late":True},{"overrides":{"food_max_stock_cost":0}},{"overrides":{"max_stock_cost":100000},"stock":200000},{"overrides":{"skip_skus":[slot.SKU]}}):
            with self.subTest(kwargs=kwargs),tempfile.TemporaryDirectory() as td:
                out,state=self.run_main(Path(td),**kwargs)
                self.assertEqual(out["buy"],[])

    def test_record_failure_keeps_proposal_and_fast_injection_cannot_enable(self):
        with tempfile.TemporaryDirectory() as td:
            out,state=self.run_main(Path(td),fail_write=True)
            self.assertEqual(out["buy"],[{"sku":slot.SKU,"qty":1}])
        with tempfile.TemporaryDirectory() as td:
            out,state=self.run_main(Path(td),injected=True,fast=True)
            self.assertEqual(out["buy"],[])

    def test_sidecar_private_allowlist_and_zero_A(self):
        for arm,qty in [("A",0),("B",1)]:
            with tempfile.TemporaryDirectory() as td:
                out,state=self.run_main(Path(td),arm=arm)
                p=state/"measure/restock_first_slot.jsonl"
                rows=[json.loads(x) for x in p.read_text().splitlines()]
                self.assertEqual(len(rows),1)
                self.assertEqual(rows[0]["proposed_qty"],qty)
                self.assertEqual(rows[0]["position"],0 if qty else None)
                self.assertEqual(p.stat().st_mode&0o777,0o600)
                self.assertEqual(set(rows[0]),{"study","opens_at","arm","at","sku","model_active","mine_rate","deals","listed","held_waiting","guard","proposed_qty","position"})

    def test_displaced_agent_line_is_queued_without_trial_self_requeue(self):
        lines=[{"sku":"other1","qty":1},{"sku":"other2","qty":1}]
        with tempfile.TemporaryDirectory() as td:
            out,state=self.run_main(Path(td),agent_lines=lines,overrides={"max_lines":2})
            self.assertEqual(out["buy"],[{"sku":slot.SKU,"qty":1},lines[0]])
            queued=json.loads((state/"restock_queue.json").read_text())["lines"]
            self.assertEqual(queued,[lines[1]])

    def test_selected_next_window_plan_is_not_overridden(self):
        for limit,expected in [(1,[{"sku":"reserved","qty":1}]),(2,[{"sku":"reserved","qty":1}])]:
            with self.subTest(limit=limit),tempfile.TemporaryDirectory() as td:
                out,state=self.run_main(Path(td),reserved=True,overrides={"max_lines":limit})
                self.assertEqual(out["buy"],expected)

    def test_new_slot_target_is_not_an_agent_backlog_at_the_late_boundary(self):
        for arm in ("A", "B"):
            with self.subTest(arm=arm), tempfile.TemporaryDirectory() as td:
                lines=[{"sku":"other1","qty":1},{"sku":slot.SKU,"qty":3}]
                out,state=self.run_main(Path(td),arm=arm,late=True,agent_lines=lines,
                                        overrides={"food_max_stock_cost":75})
                queue=json.loads((state/"restock_queue.json").read_text())["lines"]
                self.assertEqual(out["buy"],[])
                self.assertEqual(sum(x["qty"] for x in queue if x["sku"]==slot.SKU),0)
                self.assertEqual(sum(x["qty"] for x in queue if x["sku"]=="other1"),1)

    def test_native_target_late_queue_is_preserved_in_all_arms(self):
        for arm,expired,want in [("OFF",False,3),("A",False,3),("B",False,1),("B",True,3)]:
            with self.subTest(arm=arm,expired=expired),tempfile.TemporaryDirectory() as td:
                out,state=self.run_main(Path(td),arm=arm,expired=expired,late=True,
                                        agent_lines=[{"sku":slot.SKU,"qty":3}])
                queue=json.loads((state/"restock_queue.json").read_text())["lines"]
                self.assertEqual(out["buy"],[])
                self.assertEqual(sum(x["qty"] for x in queue if x["sku"]==slot.SKU),want)

    def test_duplicate_displaced_agent_quantities_remain_exact(self):
        for arm in ("A","B"):
            with self.subTest(arm=arm),tempfile.TemporaryDirectory() as td:
                lines=[{"sku":"other1","qty":1},{"sku":"other1","qty":1},
                       {"sku":slot.SKU,"qty":3}]
                out,state=self.run_main(Path(td),arm=arm,late=True,agent_lines=lines,
                                        overrides={"food_max_stock_cost":150})
                queue=json.loads((state/"restock_queue.json").read_text())["lines"]
                self.assertEqual(sum(x["qty"] for x in queue if x["sku"]==slot.SKU),0)
                self.assertEqual(sum(x["qty"] for x in queue if x["sku"]=="other1"),2)

    def test_preopen_plan_waits_in_native_kit_before_first_rpc(self):
        import time
        import market_kit as kit
        for arm,expected in [("OFF",0),("A",0),("B",1)]:
            with self.subTest(arm=arm),tempfile.TemporaryDirectory() as td:
                out,state=self.run_main(Path(td),arm=arm,preopen=True)
                inv=json.loads((state/"inventory.json").read_text())
                self.assertIs(inv["restock"]["open"],False)
                clock=[119965.0];calls=[]
                class Reply:
                    status_code=200
                    text="offline"
                    def json(self):return {**inv,"balance":10**9,"total":75}
                class Market:
                    def call(self,method,path,role,**kwargs):
                        if method=="POST":calls.append((clock[0],kwargs["json"]))
                        return Reply()
                def sleep(seconds):clock[0]+=seconds
                with patch.object(time,"time",side_effect=lambda:clock[0]), \
                     patch.object(time,"monotonic",side_effect=lambda:clock[0]), \
                     patch.object(time,"sleep",side_effect=sleep), \
                     patch.object(kit,"run_buyer",return_value=[json.dumps(out)]), \
                     patch.object(kit,"_supply_last",[None]),patch.object(kit,"log",return_value=None):
                    kit.restock({"restock":{"cmd":"offline"}},Path(td),Market(),state,inv,[])
                self.assertEqual(len(calls),expected)
                if expected:
                    self.assertGreaterEqual(calls[0][0],120000)
                    self.assertEqual(calls[0][1],{"sku":slot.SKU,"qty":1})

    def test_unknown_window_flag_cannot_enable_new_slot(self):
        for flag in (None,"false",0):
            with self.subTest(flag=flag),tempfile.TemporaryDirectory() as td:
                out,state=self.run_main(Path(td),open_flag=flag)
                self.assertEqual(out["buy"],[])

    def test_preopen_slot_never_reopens_a_plan_closed_by_next_window(self):
        with tempfile.TemporaryDirectory() as td:
            out,state=self.run_main(Path(td),preopen=True,reserved=True,sent_trial=True)
            self.assertEqual(out["buy"],[],"native waiting plan must remain closed before open")

    def test_known_false_after_open_keeps_current_native_no_wait_contract(self):
        import time
        import market_kit as kit
        for arm,expected in [("OFF",[]),("A",[]),("B",[{"sku":slot.SKU,"qty":1}])]:
            with self.subTest(arm=arm),tempfile.TemporaryDirectory() as td:
                out,state=self.run_main(Path(td),arm=arm,open_flag=False)
                inv=json.loads((state/"inventory.json").read_text())
                self.assertIs(inv["restock"]["open"],False)
                self.assertEqual(out["buy"],expected)
                # A stale False after opens_at is allowed; native kit waits zero.
                # Its existing cash/pacing/429 checks still govern actual sending.
                with patch.object(time,"time",return_value=120001):
                    self.assertEqual(kit.until_open(inv),0)

    def test_new_slot_is_independent_of_old_first_stock_identity_and_state(self):
        import hashlib
        self.assertEqual(trial.SKU,"Gb834bbb5")
        for arm,qty in [("OFF",0),("A",0),("B",1)]:
            with self.subTest(arm=arm),tempfile.TemporaryDirectory() as td:
                old_bytes=json.dumps({"enabled":True,"study":"preserved-old",
                    "start":118200,"stop":120000,"b_parity":0}).encode()
                out,state=self.run_main(Path(td),arm=arm,old_ineligible=True)
                self.assertEqual(sum(b["qty"] for b in out["buy"] if b["sku"]=="G022ce883"),qty)
                self.assertFalse(any(b["sku"]=="Gb834bbb5" for b in out["buy"]))
                self.assertEqual(hashlib.sha256((state/trial.SWITCH).read_bytes()).digest(),
                                 hashlib.sha256(old_bytes).digest())
                self.assertEqual(trial.SKU,"Gb834bbb5")
