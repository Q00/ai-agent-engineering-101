"""Feasible allocation comparisons and reasoning before any reservation."""
import json,random,unittest
from collections import Counter
import inventory_portfolio as portfolio
from procurement_strategy import comparison_baskets,validate_review
from stock_policy import replenishment_candidates
from market_memory import events
from test_ample_stock import history
import test_inventory_portfolio as fixtures


class StrategyTests(unittest.TestCase):
    setUp=fixtures.PortfolioTests.setUp
    tearDown=fixtures.PortfolioTests.tearDown

    def baskets(self,budget=5000000,categories=None):
        inv=fixtures.inventory(0);cat=fixtures.catalog()
        winners=replenishment_candidates(cat,inv,history(),1000)
        rows=[{'sku':f's{i}','category':cat[f's{i}']['category'],'cost_ceiling':11250} for i in range(2,12)]
        return comparison_baskets(winners,rows,anchor='s2',minimum=2,minimum_categories=2,slots=10,
            units=200,budget=budget,novel_budget=budget,stocks={},categories=categories or {},pending={}),winners

    def test_examples_trade_discovery_for_depth_with_the_same_metric(self):
        examples,_=self.baskets();by={e['id']:e for e in examples}
        self.assertEqual(set(by),{'broad','balanced','depth'})
        self.assertGreater(by['broad']['novel_lines'],by['depth']['novel_lines'])
        self.assertGreater(by['depth']['approx_coverage_2h'],by['broad']['approx_coverage_2h'])
        self.assertEqual(len({e['forecast_units_2h'] for e in examples}),1)
        for example in examples:
            self.assertLessEqual(example['supply_lines'],10);self.assertLessEqual(example['units'],200)
            self.assertLessEqual(example['cost_ceiling'],5000000)
            self.assertIn('s2',example['skus'])

    def test_examples_cannot_exceed_category_room_or_cash(self):
        examples,_=self.baskets(100000,{'category1':23})
        for e in examples:
            counts=Counter(r['sku'] for r in e['lines'])
            self.assertEqual(max(counts.values()),1)
            self.assertTrue(all(r['qty']<=1 for r in e['lines'] if r['sku']=='s1'))
            self.assertLessEqual(e['cost_ceiling'],100000)
        cheap,_=self.baskets(10000)
        self.assertEqual(cheap,[])  # cannot fund the required exploration witness

    def test_safe_model_basket_proceeds_with_missing_review_recorded(self):
        inv=fixtures.inventory(0)
        calls=[]
        def model(role,prompt,schema):
            calls.append(prompt)
            example=prompt['basket_comparison']['alternatives'][-1]
            return {'action':'buy','skus':example['skus'],'replenishment':example['replenishment'],
                    'reason':'Model selected this financially valid basket.'}
        result=portfolio.run(inv,fixtures.catalog(),history(),1000,model=model,
            refresh=lambda:(inv,1000,True),state_path=self.path,rng=random.Random(1))
        self.assertTrue(result['buy'])
        self.assertEqual(len(calls),1)
        approval=next(e for e in events() if e['event']=='portfolio_approval')
        self.assertEqual(approval['allocation_review']['status'],'incomplete')
        self.assertTrue(any(e['event']=='portfolio_review_incomplete' for e in events()))
        self.assertEqual(approval['model_choice']['skus'],calls[0]['basket_comparison']['alternatives'][-1]['skus'])

    def test_missing_omission_reason_preserves_exact_selected_basket(self):
        inv=fixtures.inventory(0)
        selected=[]
        def model(role,prompt,schema):
            example=prompt['basket_comparison']['alternatives'][-1]
            selected.extend(example['skus'])
            return {'action':'buy','skus':example['skus'],'replenishment':[],
                'strategy_review':{'compared_ids':['broad','depth'],'chosen_approach':'custom',
                    'tradeoff':'Selected the small exploration basket.','exceptions':[]},
                'reason':'Model chose exploration only.'}
        result=portfolio.run(inv,fixtures.catalog(),history(),1000,model=model,
            refresh=lambda:(inv,1000,True),state_path=self.path,rng=random.Random(1))
        self.assertEqual({r['sku'] for r in result['buy']},set(selected))
        approval=next(e for e in events() if e['event']=='portfolio_approval')
        self.assertIn('omission',approval['allocation_review']['error'])

    def test_model_can_choose_depth_and_audit_records_its_actual_behavior(self):
        inv=fixtures.inventory(0)
        def model(role,prompt,schema):
            alternatives=prompt['basket_comparison']['alternatives'];e=next(e for e in alternatives if e['id']=='depth')
            return {'action':'buy','skus':e['skus'],'replenishment':e['replenishment'],
                'strategy_review':{'compared_ids':['broad','depth'],'chosen_approach':'depth',
                    'tradeoff':'Use two discovery slots and cover evidenced demand with a longer buffer.','exceptions':[]},'reason':'Cover demand while preserving exploration'}
        result=portfolio.run(inv,fixtures.catalog(),history(),1000,model=model,
            refresh=lambda:(inv,1000,True),state_path=self.path,rng=random.Random(1))
        self.assertGreater(result['buy'][0]['qty'],2)
        approval=next(e for e in events() if e['event']=='portfolio_approval')
        self.assertEqual(approval['allocation_review']['chosen_approach'],'depth')
        self.assertEqual(approval['allocation_behavior']['buffered_proven_skus'],1)
        self.assertEqual(approval['allocation_behavior']['novel_skus'],2)

    def test_named_example_cannot_hide_a_different_allocation_and_omission_needs_reason(self):
        examples,winners=self.baskets();e=examples[-1]
        review={'compared_ids':['broad','depth'],'chosen_approach':'depth','tradeoff':'Choose deeper proven coverage to reduce stockouts.','exceptions':[]}
        with self.assertRaisesRegex(ValueError,'differs'):
            validate_review(review,examples,winners,e['skus'],[])
        review['chosen_approach']='custom'
        with self.assertRaisesRegex(ValueError,'omission'):
            validate_review(review,examples,winners,e['skus'],[])
        review['exceptions']=[{'sku':'s1','reason':'Incoming food remaining lifetime is unknown; prioritize other available categories this window.'}]
        self.assertEqual(validate_review(review,examples,winners,e['skus'],[])['status'],'reviewed')


if __name__=='__main__':unittest.main()
