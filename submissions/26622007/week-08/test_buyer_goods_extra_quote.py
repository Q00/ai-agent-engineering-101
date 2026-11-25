"""Fast failed goods quotes may use one bounded extra batch; no network/model."""
import concurrent.futures
import contextlib
import io
import json
import sys
import os
import tempfile
import time
import unittest
from unittest import mock
import market_buyer as mb
import buyer_goods_quote_probe as probe

class EnabledPilot(unittest.TestCase):
    def setUp(self):
        p=mock.patch.object(probe,"ENABLED",True)
        p.start();self.addCleanup(p.stop)


class Assignments(EnabledPilot):
    def test_goods_only_window_off_retry_and_pending_balance(self):
        with tempfile.TemporaryDirectory() as md:
            def reserve(i):
                return probe.reserve(md,{"kind":"goods","order":str(i),"category":"c"},now=probe.START+1,env={})
            with concurrent.futures.ThreadPoolExecutor(8) as ex:
                arms=list(ex.map(reserve,range(8)))
            self.assertEqual(arms.count("A"),4)
            self.assertEqual(arms.count("B"),4)
            self.assertEqual(probe.reserve(md,{"kind":"goods","order":"0","category":"c"},now=probe.END+1,env={}),arms[0])
            with mock.patch.object(probe,"ENABLED",False):
                self.assertIsNone(probe.reserve(md,{"kind":"goods","order":"0","category":"c"},now=probe.START+1,env={}))
            self.assertIsNone(probe.reserve(md,{"kind":"food","order":"f"},now=probe.START,env={}))
            self.assertIsNone(probe.reserve(md,{"kind":"goods","order":"x"},now=probe.START-1,env={}))
            self.assertIsNone(probe.reserve(md,{"kind":"goods","order":"x"},now=probe.END,env={}))
            self.assertIsNone(probe.reserve(md,{"kind":"goods","order":"0"},now=probe.START,env={"BUYER_GOODS_EXTRA_QUOTE":"0"}))
    def test_corrupt_journal_is_preserved_and_returns_baseline(self):
        with tempfile.TemporaryDirectory() as md:
            with open(os.path.join(md,probe.JOURNAL),"w") as f:f.write("{")
            self.assertIsNone(probe.reserve(md,{"kind":"goods","order":"x"},now=probe.START,env={}))
            with open(os.path.join(md,probe.JOURNAL)) as f:self.assertEqual(f.read(),"{")

class AdmissionBoundary(EnabledPilot):
    def test_no_stock_stays_in_reserved_itt_and_records_arm(self):
        with tempfile.TemporaryDirectory() as md:
            for name,data in (("shops.json",[]),("order.json",{"kind":"goods","order":"empty","category":"c","acceptable":["G1"]})):
                with open(os.path.join(md,name),"w") as f:json.dump(data,f)
            argv=["market_buyer","--shops",os.path.join(md,"shops.json"),"--order",os.path.join(md,"order.json"),"--item","goods","--budget","100"]
            with mock.patch.object(sys,"argv",argv),mock.patch.object(mb.time,"time",return_value=probe.START+1),mock.patch.object(mb,"MEMORY",os.path.join(md,"mem.json")),mock.patch.dict(os.environ,{"BUYER_GOODS_EXTRA_QUOTE":"1"}),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mb.main(),0)
            with open(os.path.join(md,"measure","orders.jsonl")) as f:r=json.loads(f.readline())
            with open(os.path.join(md,probe.JOURNAL)) as f:j=json.load(f)
            self.assertEqual(r["state"],"NO_SHOP_IN_STOCK")
            self.assertEqual(r["goods_extra_quote_probe"],j["orders"]["empty"]["arm"])


class DoneThread:
    def is_alive(self):return False

class QuoteBoundary(EnabledPilot):
    def shops(self):
        return [{"name":n,"url":"https://example.invalid/"+n,"usable":[{"sku":"G1","name":"x","list_price":100}]} for n in ("a","b","c")]
    def run_quotes(self,arm="B",kind="goods",elapsed=4,first_price=90,pending=False):
        shops=self.shops();calls=[]
        def start(batch,order,budget,chance,got,ctl):
            calls.append([s["name"] for s in batch])
            for s in batch:
                s["_offer"]=s["usable"][0];s["_quote_attempted"]=True
                got[s["name"]]={"ask":first_price if s["name"]!="c" else 60,"ask_src":"metadata","closed":False}
            return {s["name"]:DoneThread() for s in batch}
        order={"kind":kind,"_goods_extra_quote_probe":arm}
        with mock.patch.object(mb,"STARTED",time.time()-elapsed),mock.patch.object(mb,"start_quotes",side_effect=start),mock.patch.object(mb,"cancel_quietly"),mock.patch.object(mb,"log"):
            best,quotes=mb.best_quote(shops[0],shops[1:],order,{},100,lambda sku:0,{"quote_shops":2},rounds=1)
        return best,calls,order
    def test_fast_failed_b_gets_one_extra_batch_but_a_food_slow_and_first_fit_do_not(self):
        best,calls,order=self.run_quotes()
        self.assertEqual(best["name"],"c")
        self.assertEqual(calls,[["a","b"],["c"]])
        self.assertIn("_goods_extra_quote_started",order)
        self.assertEqual(order["_goods_extra_quote_added"],1)
        self.assertEqual(order["_goods_extra_quote_baseline"]["shop"],"a")
        self.assertTrue(order["_goods_extra_quote_baseline"]["would_skip_at_snapshot"])
        for args in ({"arm":"A"},{"kind":"food"},{"elapsed":9},{"first_price":60}):
            best,calls,order=self.run_quotes(**args)
            self.assertEqual(len(calls),1,args)
    def test_extra_wait_is_bounded_and_late_quote_does_not_hold_order(self):
        shops=self.shops();calls=[]
        class Pending:
            def is_alive(self):return True
        def start(batch,order,budget,chance,got,ctl):
            calls.append([s["name"] for s in batch])
            for s in batch:
                s["_offer"]=s["usable"][0]
                if s["name"]!="c":got[s["name"]]={"ask":90,"closed":False}
            return {s["name"]:(Pending() if s["name"]=="c" else DoneThread()) for s in batch}
        order={"kind":"goods","_goods_extra_quote_probe":"B"}
        began=time.monotonic()
        with mock.patch.object(mb,"STARTED",time.time()),mock.patch.object(mb,"start_quotes",side_effect=start),mock.patch.object(mb,"cancel_quietly"),mock.patch.object(mb,"log"),mock.patch.object(probe,"EXTRA_WAIT",.03):
            mb.best_quote(shops[0],shops[1:],order,{},100,lambda sku:0,{"quote_shops":2},rounds=1)
        self.assertLess(time.monotonic()-began,.5)
        self.assertEqual(len(calls),2)
