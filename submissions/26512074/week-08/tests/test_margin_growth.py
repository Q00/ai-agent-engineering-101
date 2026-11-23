import json
from tests.test_integrated_strategy import Case
from strategy_feedback import market_price,quote
from strategy_policy import database
from demand_strategy import plan


class MarginGrowthTests(Case):
    def test_margin_arm_can_explore_below_wait_floor_without_below_cost_sale(self):
        self.policy.update(price_exploration_fraction=1,price_multipliers=[1.0],seller_margin_share_targets=[.9]);self.save_policy()
        self.market()
        u=dict(unit='u',sku='x',cost=50000,list_price=100000,kind='goods',grade='A')
        base=market_price(self.md,u,self.inv,self.now)
        q=quote(self.md,'margin',u,self.inv,base,self.now)
        self.assertEqual(q['ask'],55000);self.assertLess(q['ask'],base['floor']);self.assertGreater(q['ask'],u['cost'])
        with database(self.md) as c:self.assertEqual(c.execute('select arm from price_quotes').fetchone()[0],-.9)

    def test_verified_premium_champion_is_preserved(self):
        self.policy.update(price_exploration_fraction=0,seller_margin_share_targets=[.9]);self.save_policy();self.market()
        with database(self.md,True) as c:c.execute('insert into price_state values(?,?,?,?,?)',('x','A',2.0,self.now,'verified'))
        u=dict(unit='u',sku='x',cost=50000,list_price=100000,kind='goods',grade='A')
        q=quote(self.md,'premium',u,self.inv,market_price(self.md,u,self.inv,self.now),self.now)
        self.assertEqual(q['pricing_evidence']['price_arm'],2.0)

    def test_slow_grade_inventory_is_not_counted_as_fast_grade_sales(self):
        m=self.market(self.events(own=True));m['acquisition_grade_weights']={'A':.7,'B':.2,'C':.1};self.write('strategy-market.json',m)
        self.inv['units']=[dict(unit='c',sku='x',cost=50000,grade='C')]
        plan(self.md,self.inv,self.catalog,self.now,dry_run=True)
        p=json.loads((self.md/'procurement-preview.json').read_text())
        if p['candidates']:
            waits=p['candidates'][0]['grade_wait_hours'];self.assertGreater(waits['C'],waits['A'])
        else:self.assertTrue(any(x['reason']=='non_positive_risk_adjusted_margin' for x in p['excluded']))
