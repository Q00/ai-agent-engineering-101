"""Finite quantity trial: allocation boundaries, same caps, no insertion/reordering or queued treatment."""
import collections
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import restock
import restock_demand_catchup as trial

SKU = trial.SKU


class CatchupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        self.opens = 1794392600
        self.now = self.opens - 30
        self.inv = {"restock": {"opens_at": self.opens, "closes_at": self.opens + 180}}
        self.switch = dict(enabled=True, study="catchup-test", start=self.now-120,
                           stop=self.now+3480, b_parity=int(self.opens//1200)%2)
        self.cfg = {**restock.DEFAULTS, "demand_catchup": {"arm": "B"},
                    "max_units_per_sku": 20, "max_sku_share": 1, "cover_hours": 4}
        self.model = {"active": True, "skus": {SKU: {"rate": 2, "my_share": 1,
                       "mine_rate": 8, "price_ratio": 1}}}
        self.catalog = {SKU: {"kind": "food", "list_price": 100, "shelf_hours": 168}}
        self.have = collections.Counter()
        self.ranked = [(4, "before", 3), (3, SKU, 8), (2, "after", 7)]

    def write(self, changes=None):
        (self.state/trial.SWITCH).write_text(json.dumps({**self.switch, **(changes or {})}))

    def adjust(self, **overrides):
        args = dict(ranked=self.ranked, model=self.model, catalog=self.catalog, have=self.have,
                    balance=100000, cfg=self.cfg, hours_left=10, factors={}, worst=restock.worst)
        return trial.adjust(**{**args, **overrides})

    def test_finite_allocation_and_invalid_switches(self):
        self.assertIsNone(trial.config(self.state, self.inv, self.now))
        self.write()
        self.assertEqual(trial.config(self.state, self.inv, self.now)["arm"], "B")
        for now in (self.now-21, self.opens+180, self.switch["stop"]):
            self.assertIsNone(trial.config(self.state, self.inv, now))
        self.write({"b_parity": 1-self.switch["b_parity"]})
        self.assertEqual(trial.config(self.state, self.inv, self.now)["arm"], "A")
        for bad in ({"stop": self.switch["start"]+3601}, {"stop": self.switch["start"]+1799},
                    {"enabled": False}, {"start": True}, {"b_parity": True}, {"study": "private text!"}):
            self.write(bad)
            self.assertIsNone(trial.config(self.state, self.inv, self.now))
        (self.state/trial.SWITCH).write_text("bad-json")
        self.assertIsNone(trial.config(self.state, self.inv, self.now))

    def test_existing_line_only_same_order_score_and_caps(self):
        self.assertEqual(self.adjust(), [(4, "before", 3), (3, SKU, 20), (2, "after", 7)])
        self.assertEqual(self.adjust(ranked=[self.ranked[0],self.ranked[2]]), [self.ranked[0],self.ranked[2]])
        self.assertEqual(self.adjust(have=collections.Counter({SKU: 15}))[1][2], 8)
        self.assertEqual(self.adjust(cfg={**self.cfg,"max_units_per_sku":12})[1][2], 12)
        self.assertEqual(self.adjust(balance=750)[1][2], 10)
        self.assertEqual(self.adjust(hours_left=.5)[1][2], 8)
        for own in (None, 1, True, float("nan"), float("inf")):
            m={"skus":{SKU:{**self.model["skus"][SKU],"mine_rate":own}}}
            self.assertEqual(self.adjust(model=m), self.ranked)
        for on in (None, {"arm":"A"}):
            self.assertEqual(self.adjust(cfg={**self.cfg,"demand_catchup":on}),self.ranked)
        self.assertEqual(self.adjust(catalog={SKU:{**self.catalog[SKU],"kind":"goods"}}),self.ranked)

    def test_public_plan_food_cash_and_waiting_guards_still_bind(self):
        inv={"balance":100000000,"units":[]}
        cfg={**self.cfg,"max_lines":20,"explore_lines":0,"prior_break":0,
             "cash_buffer":0,"keep_cash":0,"food_max_stock_cost":750,"max_stock_cost":100000}
        with patch.object(restock.time,"time",return_value=self.now):
            answer=restock.plan(inv,self.catalog,[],cfg,self.model)
            self.assertEqual(answer["buy"],[{"sku":SKU,"qty":10}])
            blocked=restock.plan(inv,self.catalog,[],{**cfg,"cash_buffer":100000000},self.model)
            self.assertEqual(blocked["buy"],[])
            waiting=[{"sku":SKU,"qty":10,"worst":75}]
            self.assertEqual(restock.plan(inv,self.catalog,[],cfg,self.model,waiting=waiting)["buy"],[])
        self.assertEqual(restock.hard_limits.final_buy(answer["buy"], 100, self.catalog, buffer=100),[])

    def test_agent_proposal_does_not_persist_treatment_into_later_windows(self):
        inv={"balance":100000000,"units":[]}
        cfg={**self.cfg,"max_lines":20,"explore_lines":0,"cash_buffer":0,"keep_cash":0}
        seen=[]
        def decide(*args):
            seen.append(json.loads(args[4]["c0"]))
            return {"buy":[],"why":"fixed offline response"}
        with patch.object(restock.time,"time",return_value=self.now), \
             patch.object(restock.decision_memory,"outcomes",return_value=[]), \
             patch.object(restock.agent_memory,"learn_from_outcomes"), \
             patch.object(restock.agent_memory,"select",return_value=[]), \
             patch.object(restock.agent_memory,"daily"):
            restock.agent_turn(str(self.state),inv,self.catalog,[],cfg,self.model,decide=decide)
        self.assertEqual(seen[0]["buy"],[{"sku":SKU,"qty":8}])

    def test_trace_allowlist_permissions_and_failure(self):
        on={"study":"test","opens_at":self.opens,"arm":"B"}
        trial.record(self.state,on,[{"sku":SKU,"qty":20,"secret":"not retained"}])
        path=self.state/"measure/restock_demand_catchup.jsonl"
        self.assertEqual(os.stat(path).st_mode & 0o777,0o600)
        row=json.loads(path.read_text())
        self.assertEqual(set(row),{"study","opens_at","arm","sku","proposed_qty"})
        with patch.object(trial.os,"open",side_effect=OSError):
            self.assertIsNone(trial.record(self.state,on,[]))
