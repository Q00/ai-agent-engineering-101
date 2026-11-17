import copy
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from restock_policy import Config, plan
from restock_evidence import attach_pilot_state, enrich_grades
from restock_runtime import SupplyGuard, SalesStore, atomic_json


class GradePilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        self.now = 10000
        self.cfg = Config(reserve_floor=100, reserve_fraction=.5, exploration_fraction=.01,
                          exploration_budget=300, grade_aware=True, refund_risk_fraction=.05,
                          max_units=10, max_sku_units=1)
        self.inv = {'round':3, 'balance':100000, 'units':[], 'surge':[],
                    '_surge_observed_at':self.now,
                    'restock':{'open':True,'opens_at':9900,'closes_at':11000}}
        self.cat = {'x':{'kind':'goods','category':'goods','list_price':100}}
        self.dem = [{'category':'goods'}]
        self.history = {'market_run':'r','observed_since':9000,'as_of':10000,'events':[]}

    def event(self, ledger, grade, price, cost=70):
        return {'id':f'r:seller:{ledger}','market_run':'r','role':'seller','round':3,
                'settled':True,'sku':'x','unit':f'u{ledger}','grade':grade,
                'price':price,'cost':cost,'ts':9900,'qty':1}

    def result(self, history=None, inv=None, cfg=None):
        return plan(inv or self.inv,self.cat,self.dem,history,now=self.now,cfg=cfg or self.cfg)

    def test_operating_config_caps_turnover_purchases(self):
        config = Config(**json.loads((Path(__file__).resolve().parents[1]/'small_batch.json').read_text()))
        self.assertTrue(config.grade_aware)
        self.assertFalse(config.require_recent_sales)
        self.assertEqual(config.exploration_budget,500000)
        self.assertEqual(config.purchase_budget,500000)
        self.assertTrue(config.turnover_mode)
        self.assertEqual(config.exploration_fraction,.2)
        self.assertEqual(config.refund_risk_fraction,0)
        self.assertEqual(config.max_sku_units,1)

    def test_sales_store_persists_grade_and_rejects_conflicting_grade(self):
        store=SalesStore(self.state/'restock_sales.json','r',clock=lambda:self.now)
        store.record(ledger=1,sku='x',price=62,unit='u1',cost=60,grade='B')
        event=json.loads(store.path.read_text())['events'][0]
        self.assertEqual(event['grade'],'B')
        with self.assertRaises(ValueError):
            store.record(ledger=1,sku='x',price=62,unit='u1',cost=60,grade='A')

    def test_grade_price_and_cost_are_not_pooled_or_cherry_picked(self):
        self.history['events']=[self.event(1,'A',120,70), self.event(2,'B',62,60)]
        result=self.result(self.history)
        self.assertEqual(result['buy'],[])
        economics=result['skipped'][0]['economics']
        self.assertEqual(economics['grade_scenarios']['A']['estimated_acquisition_cost'],70)
        self.assertEqual(economics['grade_scenarios']['B']['estimated_acquisition_cost'],60)
        self.assertEqual(economics['selected_scenario'],'B')
        self.assertLess(economics['estimated_margin'],0)
        self.assertEqual(economics['next_grade'],'unknown')

    def test_positive_same_grade_history_can_restock_with_full_cash_envelope(self):
        self.history['events']=[self.event(1,'A',120,70)]
        result=self.result(self.history)
        self.assertEqual(result['buy'],[{'sku':'x','qty':1}])
        self.assertEqual(result['estimated_cost_upper'],113)
        self.assertGreater(result['cash_commitment_upper'],113)

    def test_unknown_grade_is_not_silently_mixed(self):
        self.history['events']=[self.event(1,None,120)]
        result=self.result(self.history)
        self.assertEqual(result['buy'],[])
        self.assertEqual(result['skipped'][0]['reason'],'missing_grade_cost_evidence')

    def test_pilot_cap_uses_approval_cost_and_buffers_not_estimated_cost(self):
        result=self.result(cfg=replace(self.cfg, exploration_budget=100))
        self.assertEqual(result['buy'],[])
        result=self.result()
        self.assertEqual(result['buy'],[{'sku':'x','qty':1}])
        self.assertLessEqual(result['cash_commitment_upper'],300)
        self.assertEqual(self.result(inv={**self.inv,'_exploration_remaining':0})['buy'],[])
        self.assertEqual(self.result(inv={**self.inv,'_exploration_occupied':True})['buy'],[])

    def test_grades_join_only_exact_immutable_evidence_without_rewriting_history(self):
        event=self.event(1,None,120)
        self.history['events']=[event]
        original=copy.deepcopy(self.history)
        evidence={'market_run':'r','ledger':1,'unit':'u1','sku':'x','price':120,
                  'actual_cost':70,'settled_grade':'A'}
        folder=self.state/'history/settlements';folder.mkdir(parents=True)
        path=folder/(hashlib.sha256(b'r').hexdigest()[:16]+'-1.json')
        path.write_text(json.dumps(evidence))
        self.assertEqual(enrich_grades(self.state,self.history)['events'][0]['grade'],'A')
        self.assertEqual(self.history,original)
        evidence['unit']='other';path.write_text(json.dumps(evidence))
        self.assertIsNone(enrich_grades(self.state,self.history)['events'][0]['grade'])

    def test_pilot_slot_requires_exact_settlement_and_absent_inventory(self):
        control={'market_run':'r','exploration':{'status':'reconciled','sku':'x','unit':{'unit':'u1'}},
                 'exploration_commitment_used':120}
        inv=copy.deepcopy(self.inv)
        attach_pilot_state(inv,control,self.history,self.cfg)
        self.assertTrue(inv['_exploration_occupied']) # missing stock alone never means sold
        self.history['events']=[self.event(1,'B',75)]
        attach_pilot_state(inv,control,self.history,self.cfg)
        self.assertFalse(inv['_exploration_occupied'])
        self.assertEqual(inv['_exploration_remaining'],180)
        inv['units']=[{'unit':'u1'}]
        attach_pilot_state(inv,control,self.history,self.cfg)
        self.assertTrue(inv['_exploration_occupied'])

    def test_runtime_budget_and_unsold_lock_survive_restart_and_new_window(self):
        test=self
        class Market:
            posts=0
            def call(self,method,path,role,**kwargs):
                if method=='POST':
                    self.posts+=1
                    test.inv['units']=[{'unit':f'u{self.posts}','sku':'x','kind':'goods','grade':'B','cost':60}]
                    test.inv['balance']-=60
                    return SimpleNamespace(status_code=200,json=lambda:{'total':60})
                return SimpleNamespace(status_code=200,json=lambda:copy.deepcopy(test.inv))
        market=Market()
        proposal=self.result()
        propose=lambda *args:proposal # Even a stale hook cannot bypass runtime guards.
        guard=SupplyGuard(self.state,'r',self.cfg,clock=lambda:self.now)
        self.assertEqual(guard.step(market,propose,self.cat,lambda inv:10000)['reason'],'bought')
        used=guard.read()['exploration_commitment_used']
        self.now+=20
        guard=SupplyGuard(self.state,'r',self.cfg,clock=lambda:self.now)
        self.assertEqual(guard.step(market,propose,self.cat,lambda inv:10000)['reason'],'unsold_or_unconfirmed_exploration')
        self.inv['units']=[]
        self.now+=20
        self.assertEqual(guard.step(market,propose,self.cat,lambda inv:10000)['reason'],'unsold_or_unconfirmed_exploration')
        store=SalesStore(self.state/'restock_sales.json','r',clock=lambda:self.now)
        store.record(ledger=1,sku='x',price=62,unit='u1',cost=60,grade='B')
        self.assertEqual(guard.step(market,propose,self.cat,lambda inv:10000)['reason'],'bought')
        store.record(ledger=2,sku='x',price=62,unit='u2',cost=60,grade='B')
        self.inv['units']=[];self.now+=1000
        self.inv['restock']={'open':True,'opens_at':self.now-1,'closes_at':self.now+100}
        restarted=SupplyGuard(self.state,'r',self.cfg,clock=lambda:self.now)
        self.assertEqual(restarted.read()['exploration_commitment_used'],2*used)
        self.assertEqual(restarted.step(market,propose,self.cat,lambda inv:10000)['reason'],'exploration_budget_exhausted')
        self.assertEqual(market.posts,2)

    def test_refusal_releases_pilot_budget_but_timeout_keeps_intent(self):
        proposal=self.result()
        for timeout in (False,True):
            with self.subTest(timeout=timeout),tempfile.TemporaryDirectory() as tmp:
                def call(method,*args,**kwargs):
                    if method=='GET':return SimpleNamespace(status_code=200,json=lambda:copy.deepcopy(self.inv))
                    if timeout:raise TimeoutError()
                    return SimpleNamespace(status_code=429,json=lambda:{'wait':10})
                guard=SupplyGuard(Path(tmp),'r',self.cfg,clock=lambda:self.now)
                guard.step(SimpleNamespace(call=call),lambda *args:proposal,self.cat,lambda inv:10000)
                c=guard.read()
                self.assertEqual(bool(c['pending']),timeout)
                self.assertEqual(c['exploration_commitment_used']>0,timeout)
