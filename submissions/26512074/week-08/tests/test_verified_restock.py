import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from optimizer import db
from profit_strategy import replenish,capital


class VerifiedRestockTests(unittest.TestCase):
    def plan(self,directory,proven,paused=False,risk_profit=None,listed=200000,recent=False):
        now=50000;md=Path(directory);events=[]
        inv=dict(balance=6000000,units=[dict(unit='slow',sku='slow',cost=30000000)],restock=dict(open=True))
        with db(md) as c:
            for i in range(5):
                c.execute('insert into units values(?,?,?,?)',(str(i),'x',100000,44000))
                if proven:
                    c.execute('insert into trials(task,sku,unit,at,cost,settled_at,price,margin) values(?,?,?,?,?,?,?,?)',
                              (str(i),'x',str(i),44500,100000,45000,140000,40000))
                events.append(dict(seq=i,kind='settle',round=3,ts=49000 if recent else 45000,data=dict(shop='totato2203-r3',sku='x',unit=str(i),amount=140000,warranty_hours=0)))
        state={'share':0 if paused else .25,'stage':0,'paused_until':60000 if paused else 0}
        if risk_profit is not None:state['metrics']=dict(mature_sales=21,net_after_refunds_storage=risk_profit,roi=.38,refund_rate=0,stale_high_units=0,defect_rate=.143)
        with patch('high_value_growth.update',return_value=state):
            result=replenish(inv,{'x':dict(kind='goods',list_price=listed)},events,now,md)
        return result,json.loads((md/'procurement-preview.json').read_text())

    def test_verified_sku_can_restock_when_ordinary_allocation_is_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            result,preview=self.plan(directory,True)
            self.assertEqual(preview['limits']['normal_budget'],0)
            self.assertTrue(result['buy'])
            self.assertIn('x',preview['verified_skus'])
            self.assertLessEqual(result['buy'][0]['qty'],16)

    def test_unverified_sku_does_not_bypass_ordinary_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            result,preview=self.plan(directory,False)
            self.assertEqual(result['buy'],[])
            self.assertTrue(any(e['reason']=='allocation_budget_below_unit_cost' for e in preview['excluded']))

    def test_verified_lower_price_bestseller_uses_proven_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            result,preview=self.plan(directory,True,listed=150000)
            self.assertEqual(preview['limits']['normal_budget'],0)
            self.assertGreater(result['buy'][0]['qty'],1)
            self.assertTrue(preview['candidates'][0]['verified_restock'])

    def test_recent_paid_demand_acceleration_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            result,preview=self.plan(directory,True,recent=True)
            self.assertEqual(preview['candidates'][0]['sales_per_hour'],3.75)
            self.assertGreater(result['buy'][0]['qty'],1)

    def test_profitable_verified_restock_is_separate_from_speculative_pause(self):
        with tempfile.TemporaryDirectory() as directory:
            result,preview=self.plan(directory,True,paused=True)
            self.assertEqual(preview['limits']['high_value_budget'],0)
            self.assertTrue(result['buy'])

    def test_full_warranty_and_emergency_cash_stay_reserved(self):
        inv=dict(balance=10000000,units=[],bankrupt_line=500000)
        result=capital(inv,4000000)
        self.assertGreaterEqual(result['cash_reserve'],4750000)
        self.assertLess(result['cash_reserve'],6000000)

    def test_profitable_defect_warning_permits_one_unit_trial(self):
        with tempfile.TemporaryDirectory() as directory:
            result,preview=self.plan(directory,False,paused=True,risk_profit=1000000)
            self.assertEqual(preview['limits']['normal_budget'],0)
            self.assertEqual(result['buy'],[{'sku':'x','qty':1}])
            self.assertLessEqual(preview['limits']['exploration_budget'],preview['limits']['budget']*.35)

    def test_realized_loss_does_not_permit_trial(self):
        with tempfile.TemporaryDirectory() as directory:
            result,preview=self.plan(directory,False,paused=True,risk_profit=-1)
            self.assertEqual(result['buy'],[])
