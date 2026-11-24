import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from restock_policy import acquisition_upper, Config, plan
from restock_runtime import SupplyGuard


class SurgeTests(unittest.TestCase):
    def test_max_surge_rounds_each_unit(self):
        self.assertEqual(acquisition_upper(100),113)
        self.assertEqual(acquisition_upper(1000),1125)

    def test_book_does_not_fund_stock_and_refunds_are_reserved(self):
        inv={"round":3,"balance":1000,"book":9999999,"refund_reserved":900,"units":[],
             "restock":{"open":True,"opens_at":0,"closes_at":200}}
        r=plan(inv,{},[],now=10,cfg=Config(reserve_floor=0,reserve_fraction=.5))
        self.assertEqual(r["budget"],0)

    def test_legitimate_surge_cost_reconciles_without_permanent_pause(self):
        with tempfile.TemporaryDirectory() as tmp:
            inv={"round":3,"balance":1000,"units":[],"restock":{"open":True,"opens_at":0,"closes_at":200}}
            calls=[]
            def call(method,*args,**kw):
                if method=="POST": calls.append(kw); return SimpleNamespace(status_code=200,json=lambda:{"total":110})
                return SimpleNamespace(status_code=200,json=lambda:inv)
            guard=SupplyGuard(Path(tmp),"test",Config(reserve_floor=0),clock=lambda:10)
            result=guard.step(SimpleNamespace(call=call),lambda *a:{"buy":[{"sku":"x","qty":1}]},
                              {"x":{"list_price":100}},lambda inv:200)
            self.assertEqual(result["reason"],"bought")
            self.assertIsNone(guard.read()["pending"])
            self.assertEqual(guard.read()["remaining"],87)

    def test_refund_reservation_blocks_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            inv={"round":3,"balance":1000,"refund_reserved":450,"units":[],"restock":{"open":True,"opens_at":0,"closes_at":200}}
            def call(method,*args,**kw):
                self.assertEqual(method,"GET")
                return SimpleNamespace(status_code=200,json=lambda:inv)
            guard=SupplyGuard(Path(tmp),"test",Config(reserve_floor=0),clock=lambda:10)
            result=guard.step(SimpleNamespace(call=call),lambda *a:{"buy":[{"sku":"x","qty":1}]},
                              {"x":{"list_price":100}},lambda inv:200)
            self.assertEqual(result["reason"],"cash_changed_before_post")
