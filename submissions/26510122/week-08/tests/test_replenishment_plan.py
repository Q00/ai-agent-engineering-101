from collections import Counter
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import market_memory
import replenishment_plan as plans
import restock


PRODUCT={'kind':'food','list_price':1000,'shelf_hours':72,'name':'Example food'}
OBS={'observed_visits':10,'observed_no_stock_visits':0,'observed_visit_rate_while_stocked':None}


def plan(now=1000):
    return plans.create(sku='F1',product=PRODUCT,candidate={'qty':2,'target_stock':2},
                        approval={'valid_for_seconds':600,'max_total_qty':4},observation=OBS,now=now)


class PlanTests(unittest.TestCase):
    def setUp(self):
        # This class tests active-market behavior, independently of today's date.
        # Production and the dedicated close-boundary tests keep the real deadline.
        synthetic_end = time.time() + 48 * 3600
        for target in ('capital_policy.ROUND_END', 'newsvendor_tool.ROUND_END'):
            deadline = patch(target, synthetic_end)
            deadline.start()
            self.addCleanup(deadline.stop)

    def test_exact_unstocked_requests_enter_replenishment_candidates_once(self):
        history=[{'event':'request_seen','task_id':'t1','requested_skus':['F1']},
                 {'event':'no_stock','task_id':'t1','requested_skus':['F1']}]
        candidates=restock.choose_candidates({'F1':PRODUCT},{'units':[]},history)
        self.assertEqual(candidates[0]['sku'],'F1')
        self.assertEqual(candidates[0]['observed_shop_requests'],1)

    def test_existing_grade_change_invalidates_approval(self):
        p=plan();p['unit_grades_at_approval']={'u1':'A'}
        result,reason=plans.execute(p,inventory={'round':3,'balance':5_000_000,'units':[{'unit':'u1','sku':'F1','grade':'B','expires_at':10000}]},product=PRODUCT,observation=OBS,now=1000)
        self.assertIsNone(result);self.assertEqual(reason,'grade_changed')

    def test_grade_change_of_newly_arrived_unit_is_also_detected(self):
        p=plan();inv={'round':3,'balance':5_000_000,'units':[{'unit':'new','sku':'F1','grade':'A','expires_at':10000}]}
        plans.execute(p,inventory=inv,product=PRODUCT,observation=OBS,now=1000)
        inv['units'][0]['grade']='B'
        result,reason=plans.execute(p,inventory=inv,product=PRODUCT,observation=OBS,now=1060)
        self.assertIsNone(result);self.assertEqual(reason,'grade_changed')

    def test_stockout_window_rolloff_cannot_hide_new_stockouts(self):
        p=plan();p['observed_no_stock_at_approval']=20
        observation={**OBS,'observed_no_stock_visits':3,'recent_stockout_times':[1001,1002,1003]}
        result,reason=plans.execute(p,inventory={'round':3,'balance':5_000_000,'units':[]},product=PRODUCT,observation=observation,now=1060)
        self.assertIsNone(result);self.assertEqual(reason,'new_stockout_evidence')

    def test_corrupt_cache_fails_closed(self):
        p=plan();p['emitted_qty']='unknown'
        result,_=plans.execute(p,inventory={'round':3,'balance':5_000_000,'units':[]},product=PRODUCT,observation=OBS,now=1000)
        self.assertIsNone(result)

    def test_reuse_obeys_total_budget_and_waits_for_new_snapshot(self):
        p=plan();inv={'round':3,'balance':5_000_000,'units':[]}
        first,_=plans.execute(p,inventory=inv,product=PRODUCT,observation=OBS,now=1000,snapshot_time=1000)
        self.assertEqual(first,{'buy':[{'sku':'F1','qty':2}]})
        again,reason=plans.execute(p,inventory=inv,product=PRODUCT,observation=OBS,now=1001,snapshot_time=1000)
        self.assertEqual(again,{'buy':[]});self.assertEqual(reason,'awaiting_inventory_refresh')
        # Another intent consumes the remaining authorized quantity, even if a supply failed.
        second,_=plans.execute(p,inventory=inv,product=PRODUCT,observation=OBS,now=1060,snapshot_time=1060)
        self.assertEqual(second,first)
        third,reason=plans.execute(p,inventory=inv,product=PRODUCT,observation=OBS,now=1120,snapshot_time=1120)
        self.assertIsNone(third);self.assertEqual(reason,'authorized_budget_exhausted')

    def test_changes_invalidate_instead_of_changing_model_target(self):
        inv={'round':3,'balance':5_000_000,'units':[]}
        cases=[({'now':1600},'expired_or_invalid_plan'),
               ({'product':dict(PRODUCT,list_price=1200)},'catalog_changed'),
               ({'observation':dict(OBS,observed_no_stock_visits=3)},'new_stockout_evidence'),
               ({'inventory':dict(inv,units=[{'sku':'F1','grade':'C','expires_at':1500}])},'expiry_requires_reassessment')]
        for override,expected in cases:
            with self.subTest(expected=expected):
                kwargs={'inventory':inv,'product':PRODUCT,'observation':OBS,'now':1000,**override}
                value,reason=plans.execute(plan(),**kwargs)
                self.assertIsNone(value);self.assertEqual(reason,expected)

    def test_cash_reserve_and_sparse_approval_are_hard_limits(self):
        p=plan();inv={'round':3,'balance':plans.MIN_BALANCE+500,'units':[]}
        result,_=plans.execute(p,inventory=inv,product=PRODUCT,observation=OBS,now=1000)
        self.assertEqual(result,{'buy':[]})
        sparse=plans.create(sku='F1',product=PRODUCT,candidate={'qty':1,'target_stock':1},
                            approval={'valid_for_seconds':600,'max_total_qty':20},
                            observation=dict(OBS,observed_visits=0),now=1000)
        self.assertEqual(sparse['max_total_qty'],2)

    def test_restocker_reuses_approved_plan_without_model_or_ledger_call(self):
        with tempfile.TemporaryDirectory() as directory:
            previous=Path.cwd();os.chdir(directory)
            try:
                state=Path('.market');state.mkdir()
                path=state/'inventory.json'
                path.write_text(json.dumps({'round':3,'balance':5_000_000,'units':[]}))
                (state/'catalog.json').write_text(json.dumps({'F1':PRODUCT}))
                choice={'action':'buy','sku':'F1','expected_sale_price':800,'demand_mean':1.2,'reason':'explore'}
                approval={'action':'approve','candidate_id':'base-600-repeat','reason':'bounded reuse'}
                with patch.object(restock,'decide_json',side_effect=[choice,approval]) as model,patch.object(restock,'fetch_market_sales',return_value=Counter()) as ledger:
                    first=restock.run(state)
                    self.assertEqual(first,{'buy':[{'sku':'F1','qty':1}]})
                    # Kit publishes a fresh snapshot after supply/settlement; a second unit is authorized.
                    os.utime(path,(time.time(),time.time()))
                    second=restock.run(state)
                    self.assertEqual(second,first)
                    self.assertEqual(model.call_count,2)
                    self.assertEqual(ledger.call_count,1)
                decision=next(e for e in market_memory.events() if e['event']=='restock_decision')
                self.assertEqual(decision['approval']['candidate_id'],'base-600-repeat')
            finally:os.chdir(previous)

    def test_unavailable_approver_does_not_emit_tool_chosen_purchase(self):
        with tempfile.TemporaryDirectory() as directory:
            previous=Path.cwd();os.chdir(directory)
            try:
                state=Path('.market');state.mkdir()
                (state/'inventory.json').write_text(json.dumps({'round':3,'balance':5_000_000,'units':[]}))
                (state/'catalog.json').write_text(json.dumps({'F1':PRODUCT}))
                choice={'action':'buy','sku':'F1','expected_sale_price':800,'demand_mean':1.2,'reason':'explore'}
                with patch.object(restock,'decide_json',side_effect=[choice,RuntimeError('offline')]),patch.object(restock,'fetch_market_sales',return_value=Counter()):
                    self.assertEqual(restock.run(state),{'buy':[]})
                self.assertFalse(restock.PLAN.exists())
            finally:os.chdir(previous)

    def test_stable_scenario_renewal_needs_only_one_model_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            previous=Path.cwd();os.chdir(directory)
            try:
                state=Path('.market');state.mkdir();path=state/'inventory.json'
                path.write_text(json.dumps({'round':3,'balance':5_000_000,'units':[]}))
                (state/'catalog.json').write_text(json.dumps({'F1':PRODUCT}))
                clock=[time.time()]
                choice={'action':'buy','sku':'F1','expected_sale_price':800,'demand_mean':1.2,'reason':'explore'}
                approval={'action':'approve','candidate_id':'base-600-repeat','reason':'bounded repeat'}
                with patch.object(restock,'time',SimpleNamespace(time=lambda:clock[0])),patch.object(restock,'decide_json',side_effect=[choice,approval,approval]) as model,patch.object(restock,'fetch_market_sales',return_value=Counter()):
                    for increment in (0,60,120):
                        clock[0]+=increment;os.utime(path,(clock[0],clock[0]))
                        self.assertEqual(restock.run(state),{'buy':[{'sku':'F1','qty':1}]})
                    self.assertEqual(model.call_count,3)
                    self.assertEqual(model.call_args.args[1]['phase'],'approve_replenishment_plan')
            finally:os.chdir(previous)

    def test_replan_request_never_executes_old_scenario(self):
        with tempfile.TemporaryDirectory() as directory:
            previous=Path.cwd();os.chdir(directory)
            try:
                state=Path('.market');state.mkdir()
                (state/'inventory.json').write_text(json.dumps({'round':3,'balance':5_000_000,'units':[]}))
                (state/'catalog.json').write_text(json.dumps({'F1':PRODUCT}))
                choice={'action':'buy','sku':'F1','expected_sale_price':800,'demand_mean':1.2,'reason':'explore'}
                with patch.object(restock,'decide_json',side_effect=[choice,{'action':'replan','candidate_id':None,'reason':'new scenario needed'}]),patch.object(restock,'fetch_market_sales',return_value=Counter()):
                    self.assertEqual(restock.run(state),{'buy':[]})
                self.assertFalse(restock.PLAN.exists())
                self.assertEqual(json.loads(restock.COOLDOWN.read_text())['at'],0)
            finally:os.chdir(previous)


if __name__=='__main__':unittest.main()
