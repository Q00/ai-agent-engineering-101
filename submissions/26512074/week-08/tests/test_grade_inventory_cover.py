import io
import json
from unittest.mock import patch
from tests.test_integrated_strategy import Case
from demand_strategy import plan,grade_targets,hydrate_unfilled_grades
from strategy_policy import database


class GradeInventoryCoverTests(Case):
    def test_food_B_does_not_satisfy_observed_strict_A_shortfall(self):
        self.catalog['x'].update(kind='food',shelf_hours=24)
        self.inv['units']=[dict(unit='b',sku='x',grade='B',cost=50000,expires_at=self.now+12*3600)]
        self.market(self.events(kind='food'))
        market=json.loads((self.md/'strategy-market.json').read_text())
        market['skus']['x']['unfilled_by_grade']={'A':4}
        self.write('strategy-market.json',market)
        result=plan(self.md,self.inv,self.catalog,self.now,dry_run=True)
        self.assertTrue(result['buy'])
        preview=json.loads((self.md/'procurement-preview.json').read_text())
        self.assertEqual(preview['candidates'][0]['grade_targets'],{'A':1,'B':0,'C':0})

    def test_food_without_strict_grade_requests_keeps_lifetime_cover(self):
        self.catalog['x'].update(kind='food',shelf_hours=24)
        self.inv['units']=[dict(unit='b',sku='x',grade='B',cost=50000,expires_at=self.now+12*3600)]
        self.market(self.events(kind='food'))
        self.assertEqual(plan(self.md,self.inv,self.catalog,self.now,dry_run=True)['buy'],[])

    def test_existing_B_does_not_block_observed_A_demand(self):
        self.inv['units']=[dict(unit='b',sku='x',grade='B',cost=50000)]
        self.market()
        result=plan(self.md,self.inv,self.catalog,self.now,dry_run=True)
        self.assertTrue(result['buy'])
        preview=json.loads((self.md/'procurement-preview.json').read_text())
        self.assertEqual(preview['candidates'][0]['grade_targets'],{'A':1,'B':0,'C':0})
        self.assertEqual(preview['candidates'][0]['effective_held'],0)

    def test_one_unit_service_target_does_not_demand_all_three_grades(self):
        self.assertEqual(grade_targets(1,{'A':.7,'B':.2,'C':.1}),{'A':1,'B':0,'C':0})
        self.inv['units']=[dict(unit='a',sku='x',grade='A',cost=50000)]
        self.market();self.assertEqual(plan(self.md,self.inv,self.catalog,self.now,dry_run=True)['buy'],[])

    def test_stockout_grade_lookup_is_cached_and_does_not_store_budget(self):
        with database(self.md,True) as c:c.execute('insert into unfilled_requests values(?,?,?)',('t','x',self.now-1))
        data={'result':{'history':[dict(role='ROLE_USER',parts=[dict(text='Prefer grade a')])]}}
        with patch('demand_strategy.urlopen',return_value=io.BytesIO(json.dumps(data).encode())) as request:
            hydrate_unfilled_grades(self.md,self.now);hydrate_unfilled_grades(self.md,self.now)
        request.assert_called_once()
        with database(self.md) as c:self.assertEqual(json.loads(c.execute("select value from meta where key='unfilled_grade:t'").fetchone()[0]),['A'])
