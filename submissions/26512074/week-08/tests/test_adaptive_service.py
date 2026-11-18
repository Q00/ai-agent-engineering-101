import json
from tests.test_integrated_strategy import Case
from demand_strategy import plan
from strategy_feedback import market_price


class AdaptiveServiceTests(Case):
    def test_more_selling_skus_extend_required_supply_service_cover(self):
        market=self.market(self.events(own=True))
        plan(self.md,self.inv,self.catalog,self.now,dry_run=True)
        first=json.loads((self.md/'procurement-preview.json').read_text())['candidates'][0]['service_cover_hours']
        for i in range(30):market['skus']['active'+str(i)]=dict(own_trades=1)
        self.write('strategy-market.json',market)
        plan(self.md,self.inv,self.catalog,self.now,dry_run=True)
        second=json.loads((self.md/'procurement-preview.json').read_text())['candidates'][0]['service_cover_hours']
        self.assertGreater(second,first)

    def test_age_alone_does_not_discount_same_market_value(self):
        self.market()
        unit=dict(unit='u',sku='x',kind='goods',grade='A',cost=50000,list_price=100000,supplied_at=self.now-3600)
        fresh=market_price(self.md,unit,self.inv,self.now)
        unit['supplied_at']=self.now-100*3600
        old=market_price(self.md,unit,self.inv,self.now)
        self.assertEqual(old['ask'],fresh['ask'])

    def test_overstock_does_not_increase_reservation_value(self):
        self.market(self.events(own=True))
        unit=dict(unit='u',sku='x',kind='goods',grade='A',cost=50000,list_price=100000,supplied_at=self.now-3600)
        self.inv['units']=[unit]
        scarce=market_price(self.md,unit,self.inv,self.now)
        self.inv['units']=[dict(unit,unit=str(i)) for i in range(20)]
        excess=market_price(self.md,unit,self.inv,self.now)
        self.assertLess(excess['floor'],scarce['floor'])
