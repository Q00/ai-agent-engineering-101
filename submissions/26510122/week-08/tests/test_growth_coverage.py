from test_inventory_portfolio import reviewed_model
"""Recovery must expand real choices, not just the cash ceiling."""
import json
import random
import unittest

import inventory_portfolio as portfolio
import capital_policy
from market_memory import events
from stock_policy import demand_forecast, replenishment_candidates, policy_view
from test_ample_stock import history
import test_inventory_portfolio as fixtures
from test_inventory_portfolio import catalog, inventory


class GrowthCoverageTests(unittest.TestCase):
    setUp=fixtures.PortfolioTests.setUp
    tearDown=fixtures.PortfolioTests.tearDown

    def test_recovery_overrides_high_draw_and_corrects_zero_novel_before_reserving(self):
        inv=inventory(portfolio.MIN_VARIETY)
        products=catalog()
        products.update({f'n{i}':dict(products['s0'],category=f'new{i}') for i in range(10)})
        calls=[]
        def model(role,prompt,schema):
            calls.append(prompt)
            self.assertGreater(prompt['policy']['draw'],prompt['policy']['epsilon'])
            self.assertFalse(prompt['policy']['coverage_recovery'])
            self.assertTrue(prompt['policy']['allocation_recovery'])
            self.assertEqual(prompt['exploration_contract']['trigger'],'capital_recovery')
            self.assertEqual(prompt['minimum_new_skus_if_buy'],2)
            winner=prompt['replenishment_candidates'][0]
            new=[]
            if len(calls)==2:
                self.assertIn('exploration_required',prompt['correction']['tool_validation_error'])
                self.assertFalse(json.loads(self.path.read_text())['intents'])
                anchor=prompt['exploration_anchor_sku']
                new=[anchor]+[r['sku'] for r in prompt['candidates'] if r['sku']!=anchor][:1]
            return {'action':'buy','skus':new,'replenishment':[{'sku':winner['sku'],
                'target_stock':winner['target_options'][-1]['target_stock']}],'reason':'model chooses buffered recovery'}
        result=portfolio.run(inv,products,history(),1000,model=reviewed_model(model),
            refresh=lambda:(inv,1000,True),rng=random.Random(2),state_path=self.path)
        self.assertEqual(len(calls),2)
        self.assertEqual(len(result['buy']),3)
        self.assertEqual(sum(i['kind']=='exploration' for i in json.loads(self.path.read_text())['intents']),2)
        self.assertEqual(portfolio.run(inv,products,history(),1000,model=reviewed_model(model),
            refresh=lambda:(inv,1000,True),state_path=self.path),{'buy':[]})
        self.assertEqual(len(calls),2)

    def test_model_can_allocate_more_than_two_lines_to_new_items(self):
        inv=inventory()
        def model(role,prompt,schema):
            self.assertEqual(prompt['maximum_new_skus'],8)
            anchor=prompt['exploration_anchor_sku']
            new=[anchor]+[r['sku'] for r in prompt['candidates'] if r['sku']!=anchor][:5]
            winner=prompt['replenishment_candidates'][0]
            return {'action':'buy','skus':new,'replenishment':[{'sku':winner['sku'],
                'target_stock':winner['target_options'][-1]['target_stock']}],'reason':'Six novel categories and a proven buffer'}
        result=portfolio.run(inv,catalog(),history(),1000,model=reviewed_model(model),
            refresh=lambda:(inv,1000,True),state_path=self.path,rng=random.Random(1))
        self.assertEqual(len(result['buy']),7)
        self.assertGreater(result['buy'][0]['qty'],2)
        approval=next(r for r in events() if r['event']=='portfolio_approval')
        line=approval['approved_lines'][0]
        self.assertEqual(line['coverage_hours'],2.)  # food's longer approved alternative
        self.assertEqual(line['forecast_scenario'],'cover_2h')
        self.assertGreater(line['forecast_mean'],1)

    def test_model_can_hold_for_a_concrete_risk_even_during_recovery(self):
        inv=inventory()
        result=portfolio.run(inv,catalog(),history(),1000,
            model=lambda *a:{'action':'hold','skus':[],'replenishment':[],
                'reason':'Observed spoilage and refunds exceed this basket margin'},
            refresh=lambda:(inv,1000,True),state_path=self.path,rng=random.Random(2))
        self.assertEqual(result,{'buy':[]})
        self.assertFalse(json.loads(self.path.read_text())['intents'])

    def test_multi_hour_targets_scale_with_observed_sales_and_remain_capped(self):
        inv=inventory(0)
        targets=[]
        for count in (2,10):
            view=demand_forecast('s1',catalog()['s0'],inv,history(count),1000)
            self.assertEqual([r['coverage_hours'] for r in view['growth_coverage_options']],[2.,6.])
            self.assertGreater(view['maximum_approval_target'],view['maximum_target'])
            self.assertLessEqual(view['maximum_approval_target'],20)
            targets.append(view['maximum_approval_target'])
        self.assertGreater(targets[1],targets[0])
        food=demand_forecast('s1',catalog()['s1'],inv,history(),1000)
        self.assertEqual([r['coverage_hours'] for r in food['growth_coverage_options']],[1.,2.])
        self.assertEqual(policy_view('s1',catalog()['s1'],inv,history(),1000)['stock_limit'],food['maximum_approval_target'])

    def test_met_capital_target_and_late_phase_offer_no_growth_buffer(self):
        inv=inventory(portfolio.MIN_VARIETY);inv['balance']=2_000_000
        for unit in inv['units']:unit['cost']=100000
        self.assertEqual(demand_forecast('s1',catalog()['s1'],inv,history(),1000)['growth_coverage_options'],[])
        self.assertEqual(demand_forecast('s1',catalog()['s1'],inventory(0),[],capital_policy.ROUND_END-3600)['growth_coverage_options'],[])

    def test_category_pending_stock_and_second_trial_limits_still_apply(self):
        inv=inventory(0)
        inv['units']=[{'unit':f'u{i}','sku':'other','category':'category1','grade':'A','cost':1} for i in range(22)]
        row=replenishment_candidates(catalog(),inv,history(10),1000,pending={'s1':1})[0]
        self.assertTrue(all(o['qty']<=1 for o in row['target_options']))  # 22 live + 1 reserved + 1 new
        trial=replenishment_candidates(catalog(),inventory(0),history(1),1000)[0]
        self.assertTrue(trial['second_trial'])
        self.assertEqual([o['qty'] for o in trial['target_options']],[1])


if __name__=='__main__':unittest.main()
