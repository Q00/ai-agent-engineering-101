import unittest
from shop_profit import buy_budget,recovery_price,own_events,DISPLAY

class ShopProfitTests(unittest.TestCase):
    def test_recent_ledger_preserves_two_hour_samples_beyond_eight_mb(self):
        import json,tempfile
        from pathlib import Path
        from shop_profit import recent_ledger
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'ledger.jsonl'
            with path.open('w',encoding='utf8') as stream:
                stream.write(json.dumps({'seq':1,'ts':4000})+'\n')
                for _ in range(9):
                    stream.write('x'*(1024*1024)+'\n')
                stream.write(json.dumps({'seq':2,'ts':9999})+'\n')
            self.assertEqual([e['seq'] for e in recent_ledger(path,10000)],[1,2])
    def test_overstock_and_cash_reserve_block_purchases(self):
        self.assertGreater(buy_budget({'balance':5000000,'units':[{'cost':1600000}]}),250000)
        self.assertEqual(buy_budget({'balance':600000,'units':[]}),0)
        self.assertEqual(buy_budget({'balance':1000000,'units':[{'cost':10000000}]}),0)
    def test_cash_recovery_price_uses_matching_grade_and_positive_margin(self):
        unit={'sku':'x','grade':'B','kind':'goods','cost':100,'list_price':180}
        events=[{'seq':i,'kind':'settle','round':3,'ts':1000,'data':{'sku':'x','grade':'B','amount':130}} for i in range(3)]
        events.append({'seq':99,'kind':'settle','ts':1000,'data':{'sku':'x','grade':'C','amount':1}})
        p=recovery_price(unit,{'balance':1000000,'units':[]},events,1100)
        self.assertEqual(p['market_median'],130)
        self.assertEqual(p['market_samples'],3)
        self.assertLess(p['ask'],180)
        self.assertGreater(p['floor'],100)
    def test_only_own_sales_drive_replenishment(self):
        rows=[{'data':{'shop':'other'}},{'data':{'shop':'totato2203-r3'}}]
        self.assertEqual(own_events(rows),[rows[1]])
        self.assertEqual(DISPLAY,'tomato&potato')
    def test_market_opportunity_raises_quote_without_own_price_feedback(self):
        unit={'sku':'x','grade':'B','kind':'goods','cost':100,'list_price':300}
        events=[{'seq':i,'kind':'settle','round':3,'ts':1000,
                 'data':{'shop':'competitor','sku':'x','grade':'B','amount':250}} for i in range(4)]
        events += [{'seq':i+10,'kind':'settle','round':3,'ts':1000,
                    'data':{'shop':'totato2203-r3','sku':'x','grade':'B','amount':101}} for i in range(20)]
        p=recovery_price(unit,{'balance':3000000,'units':[]},events,1100)
        self.assertGreater(p['ask'],250)
        self.assertEqual(p['market_samples'],4)

if __name__=='__main__':
    unittest.main()
