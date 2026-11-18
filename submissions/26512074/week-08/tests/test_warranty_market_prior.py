from tests.test_integrated_strategy import Case
from strategy_policy import warranty_rate,_risk_cache


class WarrantyMarketPriorTests(Case):
    def seed(self,local=None,food=False):
        self.catalog['peer']=dict(kind='goods')
        self.catalog['food']=dict(kind='food')
        self.write('catalog.json',self.catalog)
        cohorts={'peer:A':dict(mature=1000,refund_rate=.03,mean_hours=6)}
        if local:cohorts['x:A']=local
        if food:cohorts['food:A']=dict(mature=10000,refund_rate=0,mean_hours=6)
        self.write('strategy-market.json',dict(warranties=cohorts));_risk_cache.clear()

    def test_cold_goods_learns_observed_grade_risk_without_food_dilution(self):
        self.seed(food=True)
        rate=warranty_rate(self.md,'x','A',6,self.policy)
        self.assertAlmostEqual(rate,(30+4*.08)/1004)
        self.assertGreater(rate,.03);self.assertLess(rate,.04)

    def test_local_high_risk_overrides_the_grade_pool(self):
        self.seed(dict(mature=100,refund_rate=.2,mean_hours=6))
        rate=warranty_rate(self.md,'x','A',6,self.policy)
        self.assertAlmostEqual(rate,(20+4*(30+4*.08)/1004)/104)
        self.assertGreater(rate,.19)

    def test_missing_pool_keeps_configured_prior_and_six_hour_hazard_cap(self):
        self.write('catalog.json',self.catalog);_risk_cache.clear()
        self.assertAlmostEqual(warranty_rate(self.md,'x','A',6,self.policy),.08)
        self.assertAlmostEqual(warranty_rate(self.md,'x','A',72,self.policy),.08)
        self.assertEqual(warranty_rate(self.md,'x','A',0,self.policy),0)
