"""Capital expansion must reach the actual model-approved execution path."""
import copy
import json
import os
from pathlib import Path
import random
import tempfile
import unittest

import capital_policy as capital
import inventory_portfolio as portfolio
import meta_effects
import replenishment_plan as plans
import seller_meta as meta
from seller_meta_policy import DEFAULT,POLICY_VERSION,active
from market_scoring import VERSION


class CapitalUnlockTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=Path.cwd();os.chdir(self.tmp.name)
        self.addCleanup(self.tmp.cleanup);self.addCleanup(os.chdir,self.old)
        self.root=Path.cwd();self.now=1000
        Path('.market').mkdir();Path('state').mkdir();Path('logs').mkdir()
        costs=[5_656_991//137]*137;costs[-1]+=5_656_991-sum(costs)
        self.inv={'round':3,'balance':31_696_142,'units':[
            {'sku':f'old{i}','unit':f'u{i}','cost':cost,'grade':'A','category':f'c{i}'}
            for i,cost in enumerate(costs)],'restock':{'open':True,'opens_at':900,'closes_at':1200}}
        amounts=[5_570_012//41]*41;amounts[-1]+=5_570_012-sum(amounts)
        self.intents=[{'sku':f'pending{i}','qty':1,'cost_ceiling':cost,'surge_reserved':True,
            'kind':'replenishment','at':900,'status':'issued_unknown','prior_units':[],
            'window':'800:980'} for i,cost in enumerate(amounts)]
        Path('state/inventory-portfolio.json').write_text(json.dumps({'version':1,'windows':{},'intents':self.intents}))
        self.cat={'big':{'kind':'goods','category':'furniture','list_price':17_000_000,'name':'Synthetic test furniture'}}
        Path('.market/catalog.json').write_text(json.dumps(self.cat));self.fresh()
    def fresh(self):
        p=Path('.market/inventory.json');p.write_text(json.dumps(self.inv));os.utime(p,(self.now,self.now))
    def decision(self,action='change',parameter='inventory_capital_share',value=.85):
        return {'action':action,'parameter':parameter,'numeric_value':value,'focus_value':None,
            'reason':'Verified exposure capacity is binding','expected_effect':'Increase actual capital headroom','evidence':['constraint_breakdown']}
    def test_same_capital_and_unknown_commitments_have_more_actual_capacity(self):
        risk=plans.inventory_risk(self.inv,self.now)
        equity=31_696_142+5_656_991
        self.assertEqual(risk['inventory_cost_limit'],int(equity*.8))
        self.assertEqual(risk['available_to_invest'],18_655_503)
        self.assertEqual(capital.target_inventory_cost(self.inv,self.now),int(equity*.65))
        self.assertGreater(capital.target_inventory_cost(self.inv,self.now),12_000_000)
        self.assertEqual(risk['pending_cost_ceiling'],5_570_012)
        self.assertIsNone(risk['operating_policy']['total_unit_limit'])
    def test_180_stocked_units_and_pending_stock_do_not_stop_a_funded_basket(self):
        self.inv['units'] += [{'sku':f'extra{i}','unit':f'extra{i}','cost':1000,'grade':'A','category':f'extra{i}'} for i in range(43)]
        cat={'new':{'kind':'goods','category':'new','list_price':10000,'name':'Synthetic new trial'}}
        def approve(role,prompt,schema):
            self.assertEqual(prompt['maximum_new_skus'],1)
            return {'action':'buy','skus':['new'],'reason':'Funded new-category trial'}
        result=portfolio.run(self.inv,cat,[],self.now,model=approve,rng=random.Random(1),refresh=lambda:(self.inv,self.now,True))
        self.assertEqual(result['buy'],[{'sku':'new','qty':1}])
        saved=json.loads(Path('state/inventory-portfolio.json').read_text())
        self.assertEqual(saved['intents'][:41],self.intents)
    def test_meta_exposure_expansion_unblocks_the_actual_large_purchase(self):
        before=plans.inventory_risk(self.inv,self.now)['available_to_invest']
        self.assertLess(before,19_125_000)
        public=[{'kind':'settle','ts':self.now-1,'data':{'sku':'big','amount':20_000_000}}]
        result=meta.review(self.root,now=self.now,public=public,model=lambda *args:self.decision())
        self.assertEqual(result['status'],'changed')
        self.assertGreater(result['change_effect']['effective_budget_delta'],0)
        self.assertIn('big',result['change_effect']['displayed_candidates_unlocked'])
        self.assertEqual(active(self.now)['inventory_capital_share'],.85)
        def approve(role,prompt,schema):
            self.assertGreaterEqual(prompt['risk_capacity']['available_to_invest'],19_125_000)
            return {'action':'buy','skus':['big'],'reason':'Model approves within newly expanded capacity'}
        buy=portfolio.run(self.inv,self.cat,[],self.now,model=approve,rng=random.Random(1),refresh=lambda:(self.inv,self.now,True))
        self.assertEqual(buy['buy'],[{'sku':'big','qty':1}])
        self.assertEqual(json.loads(Path('state/inventory-portfolio.json').read_text())['intents'][:41],self.intents)
    def test_counterfactual_exposure_matches_runtime_and_is_not_profit(self):
        data=meta.snapshot(self.root,self.now,[])
        before={k:data['active_policy'][k] for k in DEFAULT};after={**before,'inventory_capital_share':.85}
        effect=meta_effects.evaluate(data,before,after)
        expected=int((31_696_142+5_656_991)*.85)-int((31_696_142+5_656_991)*.8)
        self.assertEqual(effect['effective_budget_delta'],expected)
        result=meta.review(self.root,now=self.now,public=[],model=lambda *a:self.decision())
        self.assertEqual(plans.inventory_risk(self.inv,self.now)['available_to_invest'],effect['after']['available_to_invest'])
        self.assertEqual(result['after']['inventory_capital_share'],.85)
        self.assertEqual(data['book']['book'],meta.snapshot(self.root,self.now,[])['book']['book'])
    def test_rolling_budget_is_an_actual_tunable_and_still_counts_prior_spend(self):
        data=meta.snapshot(self.root,self.now,[]);data['inventory_state']['rolling_exploration_reserved']=23_000_000
        prior={**DEFAULT,'rolling_exploration_budget':24_000_000}
        effect=meta_effects.evaluate(data,prior,{**prior,'rolling_exploration_budget':48_000_000})
        self.assertEqual(effect['before']['effective_exploration_budget'],1_000_000)
        self.assertEqual(effect['after']['effective_exploration_budget'],18_655_503)
        self.assertGreater(effect['effective_exploration_budget_delta'],0)
    def test_legacy_policy_migrates_without_wiping_preferences_or_pending(self):
        old={'schema_version':1,'score_version':VERSION,'version':'legacy','at':900,'expires_at':2000,
            'changed_at':1,'config':{'window_budget':12_000_000,'target_capital_share':.7,'epsilon':.35,'focus':'discovery'},
            'previous_config':{'window_budget':12_000_000,'target_capital_share':.65,'epsilon':.3,'focus':'balanced'},
            'baseline':{'at':1,'book':{'book':1}}}
        p=Path('state/seller-meta-policy.json');p.write_text(json.dumps(old))
        upgraded=active(self.now)
        self.assertEqual(upgraded['window_budget'],24_000_000)
        self.assertEqual(upgraded['inventory_capital_share'],.8)
        self.assertEqual(upgraded['target_capital_share'],.7);self.assertEqual(upgraded['epsilon'],.35)
        self.assertEqual(json.loads(p.read_text()),old)  # reads do not rewrite history
        def hold(role,prompt,schema):
            self.assertTrue(prompt['snapshot']['capital_policy_transition']['rebased'])
            self.assertIsNone(prompt['snapshot']['policy_evaluation'])
            return self.decision('hold')
        meta.review(self.root,now=self.now,public=[],model=hold)
        saved=json.loads(p.read_text())
        self.assertEqual(saved['schema_version'],2);self.assertEqual(saved['policy_contract_version'],POLICY_VERSION)
        self.assertEqual(saved['changed_at'],self.now)
        self.assertEqual(saved['config']['epsilon'],.35)
        self.assertEqual(json.loads(Path('state/inventory-portfolio.json').read_text())['intents'],self.intents)
    def test_cash_reserve_and_invalid_state_still_fail_closed(self):
        poor=copy.deepcopy(self.inv);poor['balance']=600_000
        self.assertEqual(plans.inventory_risk(poor,self.now,pending_cost=0)['available_to_invest'],0)
        Path('state/inventory-portfolio.json').write_text('{invalid')
        self.assertEqual(plans.inventory_risk(self.inv,self.now)['available_to_invest'],0)


if __name__=='__main__':unittest.main()
