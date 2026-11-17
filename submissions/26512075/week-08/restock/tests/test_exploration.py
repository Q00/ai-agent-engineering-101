import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from restock_policy import Config, plan
from restock_runtime import SupplyGuard, atomic_json


class ExplorationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        self.now = 1000
        self.cfg = Config(reserve_floor=600000, reserve_fraction=.8, spend_fraction=.01,
                          exploration_fraction=.01, max_units=10, max_sku_units=1, max_category_units=3, max_lines=3)
        self.inv = {"round":3,"balance":5000000,"units":[],"surge":[],"_surge_observed_at":1000,
                    "restock":{"open":True,"opens_at":990,"closes_at":1180}}
        self.catalog = {"x":{"kind":"goods","category":"goods","list_price":40000}}
        self.demand = [{"category":"goods"}]

    def planned(self, inv=None, remaining=None):
        return plan(inv or self.inv,self.catalog,self.demand,now=self.now,cfg=self.cfg,window_remaining=remaining)

    def test_example_separates_estimate_from_approval_and_buffers(self):
        r=self.planned()
        self.assertEqual(r["buy"],[{"sku":"x","qty":1}])
        self.assertTrue(r["exploration"])
        e=r["candidate_economics"]["x"]
        self.assertEqual((e["estimated_acquisition_cost"],e["approval_cost_upper"]),(30000,45000))
        self.assertEqual(e["estimated_margin"],2100)
        self.assertEqual(r["cash_commitment_upper"],49050)
        self.assertEqual(e["sale_price_source"],"exploration_assumption_90pct_list")

    def test_missing_stale_malformed_or_expensive_surge_cannot_explore(self):
        for changes in [{"surge":None},{"_surge_observed_at":900},
                        {"surge":[{"sku":"x","factor":True}]},{"surge":[{"sku":"x","factor":1.5}]}]:
            with self.subTest(changes=changes):
                self.assertEqual(self.planned({**self.inv,**changes})["buy"],[])

    def test_one_goods_only_and_stock_threshold_and_open(self):
        self.catalog["y"]={**self.catalog["x"]}
        self.assertEqual(sum(b["qty"] for b in self.planned()["buy"]),1)
        unit={"unit":"u","sku":"other","kind":"goods","cost":100,"grade":"B"}
        for count in range(self.cfg.max_units):
            stock=[{**unit,"unit":f"u{i}"} for i in range(count)]
            self.assertEqual(sum(b["qty"] for b in self.planned({**self.inv,"units":stock})["buy"]),1)
        stock=[{**unit,"unit":f"u{i}"} for i in range(self.cfg.max_units)]
        self.assertEqual(self.planned({**self.inv,"units":stock})["buy"],[])
        self.assertEqual(self.planned({**self.inv,"_exploration_pending":True})["buy"],[])
        self.assertEqual(self.planned({**self.inv,"restock":{**self.inv["restock"],"open":False}})["buy"],[])
        for p in self.catalog.values(): p["kind"]="food"
        self.assertEqual(self.planned()["buy"],[])

    def test_approval_needs_maximum_cost_plus_buffers(self):
        self.assertEqual(self.planned(remaining=49049)["buy"],[])
        self.assertTrue(self.planned(remaining=49050)["buy"])

    def test_runtime_rechecks_total_inventory_limit_before_post(self):
        for count in (self.cfg.max_units - 1, self.cfg.max_units):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as tmp:
                self.inv["units"]=[]
                proposal=self.planned()
                self.inv["units"]=[{"unit":f"old{i}","sku":"other","kind":"goods","grade":"B","cost":100}
                                   for i in range(count)]
                posts=[]
                def call(method,*args,**kwargs):
                    if method=="POST":
                        posts.append(kwargs)
                        return SimpleNamespace(status_code=400,json=lambda:{})
                    return SimpleNamespace(status_code=200,json=lambda:copy.deepcopy(self.inv))
                guard=SupplyGuard(Path(tmp),"r",self.cfg,clock=lambda:self.now)
                # Deliberately stale proposal: executor must independently check stock.
                guard.step(SimpleNamespace(call=call),lambda *args:proposal,self.catalog,lambda inv:50000)
                self.assertEqual(len(posts),1 if count < self.cfg.max_units else 0)

    def market(self, visible=True, cost=28000):
        test=self
        class Market:
            posts=0
            def call(self,method,path,role,**kw):
                if method=="POST":
                    self.posts+=1
                    test.inv["balance"]-=28000
                    if visible:
                        test.inv["units"]=[{"unit":"new-unit","sku":"x","kind":"goods","grade":"B",
                                            "cost":cost,"supplied_at":test.now,"expires_at":None}]
                    return SimpleNamespace(status_code=200,json=lambda:{"total":28000})
                return SimpleNamespace(status_code=200,json=lambda:copy.deepcopy(test.inv))
        return Market()

    def step(self, guard, market):
        return guard.step(market,lambda inv,remaining:self.planned(inv,remaining),self.catalog,lambda inv:self.planned(inv)["budget"])

    def test_success_records_unit_actual_cost_and_comparison(self):
        market=self.market()
        guard=SupplyGuard(self.state,"r",self.cfg,clock=lambda:self.now)
        self.assertEqual(self.step(guard,market)["reason"],"bought")
        control=guard.read()["exploration"]
        self.assertEqual(control["status"],"reconciled")
        self.assertEqual(control["unit"]["unit"],"new-unit")
        self.assertEqual(control["actual_total"],28000)
        self.assertEqual(control["actual_minus_estimated_cost"],-2000)
        self.assertTrue(Path(control["evidence"]).exists())
        self.now+=20
        self.step(guard,market)
        self.assertEqual(market.posts,1)

    def test_unmatched_supply_blocks_next_post_across_restart(self):
        market=self.market(visible=False)
        guard=SupplyGuard(self.state,"r",self.cfg,clock=lambda:self.now)
        self.assertEqual(self.step(guard,market)["reason"],"bought")
        self.assertEqual(guard.read()["exploration"]["status"],"awaiting_inventory")
        self.now+=20
        restarted=SupplyGuard(self.state,"r",self.cfg,clock=lambda:self.now)
        self.assertEqual(self.step(restarted,market)["reason"],"exploration_unit_not_identified")
        self.assertEqual(market.posts,1)

    def test_actual_cost_mismatch_does_not_unlock(self):
        market=self.market(cost=27000)
        guard=SupplyGuard(self.state,"r",self.cfg,clock=lambda:self.now)
        self.step(guard,market)
        self.now+=20
        self.assertEqual(self.step(guard,market)["reason"],"exploration_cost_or_unit_mismatch")
        self.assertEqual(market.posts,1)

    def test_surge_change_before_post_vetoes_purchase(self):
        market=self.market()
        original=market.call
        gets=0
        def call(method,*args,**kw):
            nonlocal gets
            if method=="GET":
                gets+=1
                if gets==2: self.inv["surge"]=[{"sku":"x","factor":1.5}]
            return original(method,*args,**kw)
        market.call=call
        guard=SupplyGuard(self.state,"r",self.cfg,clock=lambda:self.now)
        self.assertEqual(self.step(guard,market)["reason"],"proposal_no_longer_needed")
        self.assertEqual(market.posts,0)

    def test_plain_kit_cli_cannot_bypass_exploration_guard(self):
        import time
        now=time.time()
        self.inv["restock"]={"open":True,"opens_at":now-1,"closes_at":now+150}
        for name,value in [("inventory",self.inv),("catalog",self.catalog),("demand",self.demand)]:
            atomic_json(self.state/(name+".json"),value)
        env={**os.environ};env.pop("RESTOCK_GUARDED_RUN",None)
        p=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/"restock_policy.py"),
                          "--state",str(self.state),"--explain"],capture_output=True,text=True,env=env)
        self.assertEqual(json.loads(p.stdout),{"buy":[]})
        self.assertEqual(json.loads(p.stderr)["reason"],"exploration_requires_guarded_launcher")
