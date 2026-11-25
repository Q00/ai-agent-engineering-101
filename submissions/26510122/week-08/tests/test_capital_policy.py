from datetime import datetime, timezone
import os
from pathlib import Path
import random
import tempfile
import unittest

from capital_policy import ROUND_END, operating_policy, target_inventory_cost
import inventory_portfolio as portfolio
import inventory_exploration as startup
import replenishment_plan as plans
from stock_policy import demand_forecast
from test_inventory_portfolio import catalog


class CapitalPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.old=Path.cwd();os.chdir(self.temp.name)
        self.addCleanup(self.temp.cleanup);self.addCleanup(os.chdir,self.old)

    def inventory(self, now):
        return {'round':3,'balance':5_000_000,'units':[],
                'restock':{'open':True,'opens_at':now-10,'closes_at':now+170}}

    def history(self, now, age=100):
        at=datetime.fromtimestamp(now-age,timezone.utc).isoformat()
        result=[]
        for i in range(2):
            result += [dict(event='receipt_signed',sku='s1',unit=f'paid{i}',cost=4000,at=at),
                       dict(event='settled',sku='s1',unit=f'paid{i}',ledger=i,price=8000,at=at)]
        return result

    def test_growth_uses_80_percent_exposure_and_65_percent_soft_target(self):
        now=ROUND_END-7*86400;inv=self.inventory(now)
        inv['units']=[dict(sku='old',grade='A',cost=1_000_000)]
        risk=plans.inventory_risk(inv,now,pending_cost=0)
        self.assertEqual(risk['inventory_cost_limit'],4_800_000)
        self.assertEqual(risk['available_to_invest'],3_800_000)
        self.assertEqual(risk['cash_reserve'],600_000)
        self.assertEqual(target_inventory_cost(inv,now),3_900_000)
        self.assertEqual(risk['operating_policy']['window_budget'],24_000_000)

    def test_bankruptcy_line_and_pending_commitments_still_bind(self):
        now=ROUND_END-7*86400;inv=self.inventory(now)
        inv.update(balance=1_000_000,bankrupt_line=800_000)
        self.assertEqual(plans.inventory_risk(inv,now,pending_cost=0)['available_to_invest'],100_000)
        self.assertEqual(plans.inventory_risk(inv,now,pending_cost=800_000)['available_to_invest'],0)

    def test_goods_holding_costs_have_a_cash_buffer_through_deadline(self):
        now=ROUND_END-10*86400;inv=self.inventory(now)
        inv['units']=[dict(sku='goods',kind='goods',grade='A',cost=1_000_000)]
        risk=plans.inventory_risk(inv,now,pending_cost=0)
        self.assertEqual(risk['storage_cash_buffer'],100_000)
        self.assertEqual(risk['cash_reserve'],700_000)

    def test_book_keeps_risk_bounds_until_the_actual_round_end(self):
        for remaining,phase in [(73,'growth'),(72,'harvest'),(24,'final_day'),(2,'closing')]:
            policy=operating_policy(ROUND_END-remaining*3600)
            self.assertEqual((policy['phase'],policy['inventory_capital_share']),(phase,.80))
            self.assertTrue(policy['allow_new_skus']);self.assertTrue(policy['allow_replenishment'])
            self.assertEqual(policy['score_weights'],{'book':.5,'revenue':.3,'stars':.2})
        ended=operating_policy(ROUND_END)
        self.assertFalse(ended['allow_replenishment']);self.assertEqual(ended['window_budget'],0)
        inv=self.inventory(ROUND_END-3600)
        self.assertGreater(plans.inventory_risk(inv,ROUND_END-3600,pending_cost=0)['available_to_invest'],0)

    def test_cached_plan_remains_eligible_before_the_actual_deadline(self):
        now=ROUND_END-7210
        observation={'proven':True,'stock_limit':2,'observed_visits':5,'observed_no_stock_visits':0,
                     'observed_visit_rate_while_stocked':None}
        p=plans.create(sku='s1',product=catalog()['s1'],candidate={'qty':1,'target_stock':1},
                       approval={'valid_for_seconds':600,'max_total_qty':1},observation=observation,now=now)
        result,reason=plans.execute(p,inventory=self.inventory(now),product=catalog()['s1'],observation=observation,now=ROUND_END-7199)
        self.assertEqual((result,reason),({'buy':[{'sku':'s1','qty':1}]},'approved_plan'))

    def test_late_period_can_replenish_proven_stock_under_book(self):
        now=ROUND_END-48*3600;inv=self.inventory(now)
        def model(role,prompt,schema):
            self.assertEqual(prompt['operating_policy']['phase'],'harvest')
            winner=prompt['replenishment_candidates'][0]
            anchor=prompt['exploration_anchor_sku']
            new=([anchor]+[r['sku'] for r in prompt['candidates'] if r['sku']!=anchor])[:prompt['minimum_new_skus_if_buy']]
            return {'action':'buy','skus':new,'replenishment':[{'sku':winner['sku'],
                'target_stock':winner['target_options'][-1]['target_stock']}],'reason':'Sell before deadline'}
        result=portfolio.run(inv,catalog(),self.history(now),now,model=model,
                             refresh=lambda:(inv,now,True),rng=random.Random(2))
        self.assertTrue(result['buy'])
        self.assertIn('s1',{r['sku'] for r in result['buy']})

    def test_forecast_is_not_clipped_to_zero_terminal_sale_capacity(self):
        now=ROUND_END-3*3600;inv=self.inventory(now)
        forecast=demand_forecast('s1',catalog()['s1'],inv,self.history(now,age=5*3600),now)
        self.assertEqual(forecast['terminal_sale_capacity_estimate'],0)
        self.assertGreater(forecast['maximum_target'],0)

    def test_final_cutoff_blocks_portfolio_and_startup_calls(self):
        now=ROUND_END;inv=self.inventory(now)
        def model(*_):self.fail('No new buying calls near market end')
        self.assertEqual(portfolio.run(inv,catalog(),[],now,model=model,refresh=lambda:(inv,now,True)),{'buy':[]})
        self.assertIsNone(startup.run(inv,catalog(),[],{},now,model=model))


if __name__=='__main__':unittest.main()
