import ast
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import buyer_first95_trial as trial

class TrialTests(unittest.TestCase):
    def call(self, **kw):
        v=dict(baseline={"shop_price":100,"action":"offer","price":90},
               history=[], limit=100, steps=(.9,), order={"order":"o1","kind":"goods","_buyer_started_at":100},
               shop="fixture", sku="G123", probe=False, now=100, log=lambda _:None)
        v.update(kw)
        return trial.turn(**v)

    def test_first_offer_and_measurement(self):
        events=[]
        order={"order":"o1","kind":"goods","_buyer_started_at":100}
        with patch.multiple(trial, ENABLED=True, START=100, END=1900), patch.object(trial,"arm",return_value="B"):
            result=self.call(order=order,log=events.append)
        self.assertEqual(result, {"shop_price":100,"action":"offer","price":95})
        self.assertEqual(len(events),2)
        measured=trial.measurement(order,{"history":[("shop asked",100),("you offered",95)]})
        self.assertEqual(measured["matches"][0]["history_offer_attempt"],95)

    def test_guards_and_window(self):
        baseline={"shop_price":100,"action":"offer","price":90}
        with patch.multiple(trial, ENABLED=True, START=100, END=1900):
            for changes in ({"probe":True},{"shop":"11im"},{"limit":1},{"limit":True},
                {"limit":10**1000},{"steps":(.8,)},{"history":[("you offered",1)]},
                {"history":[("shop asked",101)]},{"now":1900},{"now":99},{"now":True},
                {"baseline":None},{"baseline":False},
                {"baseline":dict(baseline,price=1)},
                {"baseline":dict(baseline,shop_price=True)},
                {"baseline":dict(baseline,shop_price=10**1000)},
                {"baseline":dict(baseline,shop_price=float("inf"))},
                {"baseline":dict(baseline,action="accept")},
                {"order":{"order":"o1","kind":"food","_buyer_started_at":100}},
                {"order":{"order":"o1","kind":"goods","_buyer_started_at":99}},
                {"order":{"order":"o1","kind":"goods","_buyer_started_at":101}}):
                with self.subTest(changes=changes):
                    events=[]
                    expected=changes.get("baseline",baseline)
                    self.assertEqual(self.call(log=events.append,**changes),expected)
                    self.assertEqual(events,[])
        self.assertIs(self.call(baseline=baseline),baseline)
        with patch.multiple(trial, ENABLED=True, START=0, END=1800):
            self.assertIs(self.call(baseline=baseline),baseline)

    def test_assignment_stable_retry_and_state(self):
        arms=set()
        with patch.multiple(trial, ENABLED=True, START=100, END=1900):
            for i in range(30):
                order={"order":f"o{i}","kind":"goods","_buyer_started_at":100}
                retry=dict(order); events=[]
                first=self.call(order=order,log=events.append)
                self.assertEqual(self.call(order=retry,now=101),first)
                self.assertEqual(self.call(order=order,now=101,log=events.append),first)
                self.assertEqual(sum(s.startswith("[first95-assignment]") for s in events),1)
                arms.add(order["_first95_trial"]["arm"])
                self.assertEqual(first["price"],95 if trial.arm(order["order"])=="B" else 90)
                order["_first95_trial"]["arm"]="invalid"
                self.assertEqual(self.call(order=order)["price"],90)
        self.assertEqual(arms,{"A","B"})

    def test_cap_and_first_ask(self):
        with patch.multiple(trial, ENABLED=True, START=100, END=1900), patch.object(trial,"arm",return_value="B"):
            self.assertEqual(self.call(limit=93,history=[("shop asked",100)])["price"],93)
            self.assertEqual(self.call(limit=90)["price"],90)
            self.assertEqual(self.call(baseline={"shop_price":101,"action":"offer","price":90})["price"],95)

class CallbackTests(unittest.TestCase):
    def test_actual_ladder_both_arms_off_counter_and_history(self):
        import market_buyer as m
        import oneshot
        import buyer
        from unittest.mock import Mock
        from pathlib import Path
        source=ast.parse(Path(m.__file__).read_text())
        callback=next(n for n in ast.walk(source) if isinstance(n,ast.FunctionDef) and n.name=="price_ladder")
        for selected,enabled,expected in (("A",True,90),("B",True,95),("B",False,90)):
            identity=next(f"o{i}" for i in range(100) if trial.arm(f"o{i}")==selected)
            order={"order":identity,"kind":"goods","_buyer_started_at":100}
            q={"ask":100,"text":"Asking 100. Warranty: 6 hours."}
            ns={**vars(m),"discount_arm":None,"probe_arm":None,"goods_first_arm":None,
                "limit":100,"steps":(.9,),"q":q,"resume":True,"order":order,
                "args":SimpleNamespace(budget=100),"shop":{"name":"fixture"},"key":"G123","r3":True}
            exec(compile(ast.Module(body=[callback],type_ignores=[]),"<actual ladder>","exec"),ns)
            ladder=ns["price_ladder"]
            def task(state,text="",ask=None):
                msg={"parts":[{"text":text}]}
                if ask is not None: msg["metadata"]={"ask":ask}
                return {"id":"fixture-task","status":{"state":state,"message":msg}}
            sent=[]
            def send(url,text,*args,**kw):
                sent.append(text)
                return task("TASK_STATE_CANCELED")
            out={"history":[],"agreed":None,"receipt":None,"holds":0}
            with patch.multiple(trial,ENABLED=enabled,START=100,END=1900), patch.object(m.time,"time",return_value=100), patch.object(m,"log"), patch.object(buyer,"send",side_effect=send):
                # Exercise real buyer.talk, not only callback source matching.
                buyer.talk("fake",task("TASK_STATE_INPUT_REQUIRED","Asking 100.",100),"fixture",100,"",out,ladder=ladder)
                self.assertEqual(ladder(98,"Counteroffer: 98.",[("shop asked",100),("you offered",expected)]),
                                 oneshot.turn(98,"Counteroffer: 98.",[("shop asked",100),("you offered",expected)],100,(.9,),100,True))
            self.assertEqual(sent,[str(expected)])
            if enabled:
                self.assertEqual(trial.measurement(order,out)["matches"][0]["history_offer_attempt"],expected)
            else:
                self.assertNotIn("_first95_trial",order)

if __name__=="__main__": unittest.main()
