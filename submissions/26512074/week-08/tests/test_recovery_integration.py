import json,tempfile,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
from after_sales import warranty_offer
from warranty_risk import locked_price_floor
from buyer_r3 import refresh_stock,prioritize_clearance,checkout_feedback
from turnover import age_terms


class RiskRecoveryTests(unittest.TestCase):
    def seed(self,d):
        Path(d,'warranty-risk.json').write_text(json.dumps(dict(version=2,at=1000,mature_warranted_sales=100,measured_rate=.1,reserve_rate=.12,cohorts={})))

    def test_risk_pool_allows_nominal_exposure_above_old_cash_limit(self):
        with tempfile.TemporaryDirectory() as d:
            self.seed(d)
            holds={str(i):dict(price=500000,expires_at=50000,issued=True) for i in range(10)}
            Path(d,'warranty-holds.json').write_text(json.dumps(holds))
            self.assertTrue(warranty_offer(d,dict(unit='new',sku='x',kind='goods',grade='A'),400000,3000000,1000))
            self.assertTrue(all(h['issued'] for k,h in json.loads(Path(d,'warranty-holds.json').read_text()).items() if k!='new'))

    def test_one_large_claim_must_remain_payable(self):
        with tempfile.TemporaryDirectory() as d:
            self.seed(d)
            self.assertFalse(warranty_offer(d,dict(unit='new',sku='x',kind='goods'),3000000,3000000,1000))

    def test_new_warranty_floor_is_locked(self):
        with tempfile.TemporaryDirectory() as d:
            self.seed(d);u=dict(sku='x',grade='A',cost=100000,supplied_at=1000)
            first=locked_price_floor(d,'t',u,6,1000,create=True)
            self.assertGreater(first,100000)
            self.assertEqual(locked_price_floor(d,'t',u,6,2000),first)

    def test_loss_proven_cohort_does_not_receive_new_warranty(self):
        with tempfile.TemporaryDirectory() as d:
            self.seed(d);path=Path(d,'warranty-risk.json');r=json.loads(path.read_text())
            r['cohorts']={'x:A:6':dict(sales=12,refund_rate=.3,reserve_rate=.32,loss_evidence=True)}
            path.write_text(json.dumps(r))
            self.assertFalse(warranty_offer(d,dict(unit='new',sku='x',kind='goods',grade='A'),400000,3000000,1000))

    def test_zero_hour_and_no_paid_warranty_are_respected(self):
        from after_sales import without_warranty
        for text in ('0-hour warranty','no paid warranty','warranty 0','무보증'):
            self.assertTrue(without_warranty(text))
        self.assertFalse(without_warranty('6-hour warranty'))

    def test_long_turnover_does_not_delay_stale_price_adjustment_for_a_day(self):
        age=age_terms(dict(kind='goods',supplied_at=1000),1000+6*3600,expected_hours=6)
        self.assertGreater(age['steps'],0)
        self.assertGreaterEqual(age['minimum_margin'],.06)

    def test_clearance_ad_only_promotes_a_quote_candidate(self):
        rows=[(1,dict(name='a',url='a'),'G12345678',100),(1,dict(name='b',url='b'),'G87654321',100)]
        posts=[dict(author='b',role='shop',ts=1000,text='clearance G87654321')]
        result=prioritize_clearance(rows,posts,1100)
        self.assertEqual(result[0][1]['name'],'b')
        self.assertEqual(result[0][3],100)

    def test_one_stock_failure_invalidates_quote_without_permanent_ban(self):
        memory={'s':{'quotes':[dict(sku='x',ask=1)]}}
        checkout_feedback([dict(shop='s',item='x',order='o',paid=False,reason='out of stock')],memory,1000)
        self.assertEqual(memory['s']['quotes'],[])
        self.assertEqual(memory['s']['stock_cooldowns']['x']['until'],1060)


class StockRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_public_stock_removes_sold_out_candidate(self):
        import io
        rows=[(1,dict(url='a'),'x',100),(1,dict(url='b'),'x',100)]
        response=io.BytesIO(json.dumps(dict(shops=[dict(url='a',stock={}),dict(url='b',stock={'x':1})])).encode())
        with patch('urllib.request.urlopen',return_value=response):result=await refresh_stock(rows)
        self.assertEqual(len(result),1);self.assertEqual(result[0][1]['url'],'b')
