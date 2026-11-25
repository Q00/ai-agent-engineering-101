import json
from pathlib import Path
import random
from unittest.mock import patch
import unittest
import test_ample_stock as ample
from test_ample_stock import history
from test_inventory_portfolio import inventory, catalog
import inventory_portfolio as portfolio
from stock_policy import replenishment_candidates, sales_evidence


class SecondTrialTests(unittest.TestCase):
    setUp = ample.AmpleStockTests.setUp
    tearDown = ample.AmpleStockTests.tearDown
    def candidates(self, rows=None, **kwargs):
        return replenishment_candidates(catalog(),inventory(0),rows if rows is not None else history(1),1000,**kwargs)

    def test_single_trial_keeps_two_sale_proven_threshold(self):
        row=self.candidates()[0]
        self.assertTrue(row['second_trial'])
        self.assertEqual(row['target_options'][0]['qty'],1)
        self.assertFalse(sales_evidence('s1',history(1),1000)['proven'])
        self.assertTrue(sales_evidence('s1',history(2),1000)['proven'])
        self.assertFalse(self.candidates(history(2))[0]['second_trial'])
        self.assertEqual(self.candidates(blocked={'s1'}),[])
        self.assertEqual(self.candidates(trialed={'s1'}),[])
        self.assertEqual(replenishment_candidates(catalog(),inventory(),history(1),1000),[])

    def test_no_trial_for_unknown_cost_loss_or_failed_second_sale(self):
        self.assertEqual(self.candidates(history(1)[1:]),[])
        rows=history(1);rows[-1]['price']=3000
        self.assertEqual(self.candidates(rows),[])
        rows=history(2);rows[-1]['price']=3000
        self.assertEqual(self.candidates(rows),[])
        with patch('stock_policy.operating_policy',return_value={'allow_replenishment':True,'allow_new_skus':False}):
            self.assertEqual(self.candidates(),[])

    def test_trial_bypasses_novel_cooldown_but_never_reissues_unknown(self):
        inv=inventory(0);path=Path('state/trial.json');path.parent.mkdir(exist_ok=True)
        # Exhaust the novel exploration budget, with no outstanding deliveries.
        intents=[];remaining=portfolio.ROLLING_BUDGET
        while remaining:
            ceiling=min(portfolio.MAX_UNIT_COST,remaining)
            qty=min(20,remaining//ceiling)
            intents.append({'sku':'s1' if not intents else f'old{len(intents)}',
                            'cost_ceiling':ceiling,'qty':qty,'prior_units':[],
                            'status':'settled','at':950,'kind':'exploration'})
            remaining-=ceiling*qty
        path.write_text(json.dumps({'version':1,'windows':{},'intents':intents}))
        prompts=[]
        def choose(role,prompt,schema):
            prompts.append(prompt)
            r=prompt['replenishment_candidates'][0]
            return {'action':'buy','skus':[], 'replenishment':[{'sku':r['sku'],'target_stock':1}], 'reason':'second unit'}
        result=portfolio.run(inv,catalog(),history(1),1000,model=choose,
                             refresh=lambda:(inv,1000,True),rng=random.Random(1),state_path=path)
        self.assertEqual(result['buy'],[{'sku':'s1','qty':1}])
        state=json.loads(path.read_text());self.assertTrue(state['intents'][-1]['second_trial'])
        self.assertEqual(prompts[0]['exploration_cost_ceiling_budget'],0)
        self.assertIn('NOT exploration_cost_ceiling_budget',prompts[0]['budget_scopes']['replenishment'])
        inv['restock']={'open':True,'opens_at':1201,'closes_at':1500}
        with patch('inventory_portfolio.replenishment_candidates',wraps=replenishment_candidates) as candidates:
            portfolio.run(inv,catalog(),history(1),1250,model=lambda *a: {'action':'hold'},
                          refresh=lambda:(inv,1250,True),rng=random.Random(1),state_path=path)
            self.assertIn('s1',candidates.call_args.args[5])
