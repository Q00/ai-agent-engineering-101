from test_inventory_portfolio import reviewed_model
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import tempfile
import unittest

import inventory_portfolio as portfolio
import replenishment_plan as plans
from stock_policy import sales_evidence, demand_forecast, replenishment_candidates, policy_view, PROVEN_STOCK
from test_inventory_portfolio import catalog, inventory
from test_ample_stock import history


class AdaptiveForecastTests(unittest.TestCase):
    def test_targets_scale_with_sales_instead_of_a_four_or_six_choice(self):
        targets = []
        inv = inventory(0)
        for count in (2,10,30):
            view = demand_forecast('s1',catalog()['s1'],inv,history(count),1000)
            targets.append(view['maximum_target'])
        self.assertLess(targets[0],targets[1])
        self.assertLess(targets[1],targets[2])
        self.assertGreater(targets[2],6)
        self.assertLessEqual(targets[2],PROVEN_STOCK)

    def test_threshold_admits_moderate_sellers_but_needs_confirmed_costs(self):
        self.assertFalse(sales_evidence('s1',history(1),1000)['maintenance_eligible'])
        self.assertTrue(sales_evidence('s1',history(2),1000)['maintenance_eligible'])
        receipts_only = [r for r in history(2) if r['event']=='receipt_signed']
        self.assertFalse(sales_evidence('s1',receipts_only,1000)['maintenance_eligible'])
        sales_only = [r for r in history(2) if r['event']=='settled']
        self.assertFalse(sales_evidence('s1',sales_only,1000)['maintenance_eligible'])

    def test_goods_with_positive_raw_spread_but_negative_stress_contribution_are_not_proven(self):
        rows = history(2)
        for event in rows:
            if event['event'] == 'receipt_signed':
                event['cost'] = 7_300
                event['supplied_at'] = 500
        result = sales_evidence('s1', rows, 1000, kind='goods')
        self.assertEqual(result['confirmed_positive_spread_sales_12h'], 2)
        self.assertEqual(result['positive_contribution_sales_12h'], 0)
        self.assertLess(result['contribution_after_holding_refund_scenario_12h'], 0)
        self.assertFalse(result['maintenance_eligible'])
        self.assertTrue(sales_evidence('s1', rows, 1000, kind='food')['maintenance_eligible'])

    def test_later_supply_increases_demand_to_cover(self):
        near = {'units':[], 'restock':{'open':False,'opens_at':1100,'closes_at':1280}}
        later = {'units':[], 'restock':{'open':False,'opens_at':2800,'closes_at':2980}}
        a = demand_forecast('s1',catalog()['s1'],near,history(10),1000)
        b = demand_forecast('s1',catalog()['s1'],later,history(10),1000)
        self.assertLess(a['protection_period']['hours'],b['protection_period']['hours'])
        self.assertLess(a['maximum_target'],b['maximum_target'])

    def test_short_term_no_sales_does_not_erase_a_slow_seller(self):
        rows = history(2)
        for row in rows:
            row['at'] = datetime.fromtimestamp(-4*3600,timezone.utc).isoformat()
        signal = sales_evidence('s1',rows,1000)
        self.assertTrue(signal['maintenance_eligible'])
        view = demand_forecast('s1',catalog()['s1'],inventory(0),rows,1000)
        self.assertGreater(view['sales_rate_per_hour'],0)
        self.assertIn('12h fallback',view['rate_source'])
        self.assertGreaterEqual(view['maximum_target'],1)

    def test_paired_stock_exposure_can_recover_stockout_censoring(self):
        now = 4000
        rows = []
        for n in range(10):
            at = 500+n*100
            rows.append({'event':'settled','sku':'s1','unit':f'u{n}','ledger':n,'price':8000,
                         'at':datetime.fromtimestamp(at,timezone.utc).isoformat()})
        for at in range(400,4001,60):
            rows.append({'event':'inventory_snapshot','round':3,'source_time':at,
                         'stock':{'s1':{'qty':1 if at<2200 else 0}}})
        view = demand_forecast('s1',catalog()['s1'],inventory(0),rows,now)
        first = view['windows']['1']
        self.assertEqual(first['covered_settlements'],10)
        self.assertGreater(first['rate_per_hour'],10)
        self.assertLess(first['in_stock_exposure_hours'],1)

    def test_current_forecast_invalidates_a_larger_cached_target(self):
        obs = {'proven':True,'stock_limit':10,'observed_visits':10,'observed_no_stock_visits':0,
               'observed_visit_rate_while_stocked':None}
        p = plans.create(sku='s1',product=catalog()['s1'],candidate={'qty':9,'target_stock':10},
                         approval={'valid_for_seconds':600,'max_total_qty':9},observation=obs,now=1000)
        result,reason = plans.execute(p,inventory=inventory(),product=catalog()['s1'],
                                     observation={**obs,'stock_limit':3},now=1001)
        self.assertIsNone(result)
        self.assertEqual(reason,'sales_evidence_changed')

    def test_eight_moderate_sellers_and_two_new_skus_fit_kit_ten_line_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            old = Path.cwd();os.chdir(directory)
            try:
                rows = []
                for i in range(8):
                    for event in history(2):
                        clone = dict(event,sku=f's{i}',unit=f"s{i}-{event['unit']}")
                        if 'ledger' in clone: clone['ledger'] += 100*i
                        rows.append(clone)
                inv = inventory(0)
                def model(role,prompt,schema):
                    self.assertEqual(len(prompt['replenishment_candidates']),8)
                    self.assertEqual(schema['properties']['replenishment']['maxItems'],8)
                    self.assertEqual(prompt['maximum_new_skus'],8)  # model allocates the shared ten lines
                    picks = [{'sku':r['sku'],'target_stock':max(o['target_stock'] for o in r['target_options'])}
                             for r in prompt['replenishment_candidates'][:8]]
                    anchor = prompt['exploration_anchor_sku']
                    new = [anchor]+[r['sku'] for r in prompt['candidates'] if r['sku']!=anchor][:1]
                    return {'action':'buy','skus':new,'replenishment':picks,'reason':'Maintain moderate sellers too'}
                result = portfolio.run(inv,catalog(),rows,1000,model=reviewed_model(model),
                                       refresh=lambda:(inv,1000,True),rng=random.Random(1))
                self.assertEqual(len(result['buy']),10)
                intents = json.loads(portfolio.STATE.read_text())['intents']
                self.assertEqual(sum(i['kind']=='replenishment' for i in intents),8)
                self.assertEqual(sum(i['kind']=='exploration' for i in intents),2)
            finally:
                os.chdir(old)


if __name__=='__main__':
    unittest.main()
