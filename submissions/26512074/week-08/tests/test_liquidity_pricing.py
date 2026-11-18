import tempfile,json,unittest
from pathlib import Path
from optimizer import db,negotiate
from liquidity_pricing import apply

class LiquidityTests(unittest.TestCase):
    def test_x2_assignment_does_not_receive_liquidity_discount(self):
        from extreme_quotes import terms,planned_arm
        with tempfile.TemporaryDirectory() as d:
            u=self.prepare(d)
            Path(d,'extreme-quote-policy.json').write_text('{"enabled":true,"allocation":100,"multiplier":2}')
            original=dict(ask=950000,floor=505000)
            self.assertEqual(planned_arm(d,'t'),'x2')
            self.assertEqual(apply(d,'t',u,{},original,0,True,44200),original)
            quote=terms(d,'t',u,original['ask'],0,True,lambda _:True,44200)
            self.assertEqual(quote['effective_quote'],1900000)
            with db(d) as c:self.assertEqual(c.execute('select version from price_policy_versions where task=?',('t',)).fetchone()[0],'original-v1')
    def prepare(self,d):
        Path(d,'liquidity-policy.json').write_text('{"enabled":true}')
        u=dict(unit='u',sku='x',kind='goods',grade='B',cost=500000,list_price=1000000,supplied_at=1000)
        with db(d) as c:c.execute('insert into trials(task,sku,unit,at,cost,inquiry) values(?,?,?,?,?,0)',('t','x','u',44000,500000))
        return u

    def test_aged_stock_positive_offer_is_not_blocked_by_market_floor(self):
        with tempfile.TemporaryDirectory() as d:
            u=self.prepare(d);p=apply(d,'t',u,{'storage_per_day':.01},dict(ask=950000,floor=505000,market_median=1000000),0,True,44200)
            self.assertEqual(p['ask'],650000);self.assertEqual(p['floor'],512500)
            deal=dict(unit=u,ask=650000,offer=600000,previous_offer=550000,warranty_hours=0,lines=['buyer: x','you: quote','buyer: 600000'])
            self.assertEqual(negotiate(d,'t',deal,p,44200)['action'],'accept')
            self.assertNotEqual(negotiate(d,'t',dict(deal,offer=500000),p,44200)['action'],'accept')

    def test_warranty_risk_floor_and_existing_task_are_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            u=self.prepare(d);original=dict(ask=950000,floor=750000,market_median=1000000)
            p=apply(d,'t',u,{},original,6,True,44200)
            self.assertEqual(p['floor'],750000);self.assertEqual(p['ask'],750000)
            self.assertEqual(apply(d,'t',u,{},dict(original,ask=2000000),6,False,60000)['ask'],750000)
            self.assertEqual(apply(d,'legacy',u,{},original,0,False,44200),original)

    def test_high_acquisition_cost_prevents_loss_even_if_target_discount_is_large(self):
        with tempfile.TemporaryDirectory() as d:
            u=dict(self.prepare(d),cost=750000)
            p=apply(d,'t',u,{'storage_per_day':.01},dict(ask=950000,floor=757500),0,True,44200)
            self.assertGreaterEqual(p['ask'],768750);self.assertGreater(p['floor'],u['cost'])
