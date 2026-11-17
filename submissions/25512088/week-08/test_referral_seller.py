import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
import httpx
import seller
from seller_ledger import Program


class SellerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def test_review_price_experiment_selects_lower_cost_same_grade_unit(self):
        units=[
            dict(unit='old-high',sku='Ged5b1b82',name='bed',grade='A',kind='goods',
                 cost=180000,supplied_at=1),
            dict(unit='new-low',sku='Ged5b1b82',name='bed',grade='A',kind='goods',
                 cost=130000,supplied_at=2),
        ]
        self.assertEqual(seller.select_unit('Ged5b1b82',units,set())['unit'],'new-low')

    def test_small_same_grade_lot_always_leads_with_lowest_cost(self):
        units=[
            dict(unit='high',sku='G00001',name='chair',grade='A',kind='goods',
                 cost=690311,supplied_at=1),
            dict(unit='low',sku='G00001',name='chair',grade='A',kind='goods',
                 cost=563091,supplied_at=2),
            dict(unit='middle',sku='G00001',name='chair',grade='A',kind='goods',
                 cost=619176,supplied_at=3),
        ]
        self.assertEqual(seller.select_unit('G00001',units,set())['unit'],'low')

    def test_open_quotes_soft_distribute_without_hiding_units(self):
        units=[dict(unit='u1',sku='G00001',name='item',category='food',grade='A',expires_at=None,supplied_at=1),
               dict(unit='u2',sku='G00001',name='item',category='food',grade='A',expires_at=None,supplied_at=2)]
        first=seller.select_unit('G00001',units,set(),{})
        second=seller.select_unit('G00001',units,set(),{first['unit']:1})
        self.assertEqual((first['unit'],second['unit']),('u1','u2'))
        self.assertIsNotNone(seller.select_unit('G00001',units,set(),{'u1':5,'u2':5}))

    def test_sale_selection_prefers_observed_conversion_order(self):
        units=[dict(unit='c',sku='G00001',name='item',category='food',grade='C',expires_at=None,supplied_at=1),
               dict(unit='b',sku='G00001',name='item',category='food',grade='B',expires_at=None,supplied_at=2),
               dict(unit='a',sku='G00001',name='item',category='food',grade='A',expires_at=None,supplied_at=3)]
        self.assertEqual(seller.select_unit('G00001',units,set(),{})['unit'],'b')
        self.assertEqual(seller.select_unit('G00001',units,{'b'}, {})['unit'],'a')
        self.assertEqual(seller.select_unit('G00001',units,{'b','a'}, {})['unit'],'c')

    def test_sale_selection_can_use_c_drain_lane(self):
        units=[dict(unit='c',sku='G00001',name='item',category='food',grade='C',expires_at=None,supplied_at=1),
               dict(unit='a',sku='G00001',name='item',category='food',grade='A',expires_at=None,supplied_at=2)]
        self.assertEqual(seller.select_unit('G00001',units,set(),{},prefer_c=True)['unit'],'c')

    def test_food_selection_honors_requested_remaining_hours(self):
        now=time.time()
        units=[
            dict(unit='short',sku='FOOD1',name='food',category='food',kind='food',grade='A',
                 expires_at=now+24*3600,supplied_at=1),
            dict(unit='long',sku='FOOD1',name='food',category='food',kind='food',grade='B',
                 expires_at=now+127*3600,supplied_at=2),
        ]
        text='I want to buy food (FOOD1). Must last at least 126.0 hours.'
        self.assertEqual(seller.required_food_hours(text),126.0)
        self.assertEqual(seller.select_unit(text,units,set(),{})['unit'],'long')
        self.assertIsNone(seller.select_unit(text,[units[0]],set(),{}))

    def test_food_requirement_accepts_bounded_market_phrasings_only(self):
        self.assertEqual(seller.required_food_hours('use within 84.0 h'),84.0)
        self.assertEqual(seller.required_food_hours('min_hours_left=36'),36.0)
        self.assertEqual(seller.required_food_hours('minimum 24 hours of shelf life'),24.0)
        self.assertEqual(seller.required_food_hours('shelf life left: 12 h'),12.0)
        self.assertEqual(seller.required_food_hours('유통기한 48시간 이상'),48.0)
        self.assertEqual(seller.required_food_hours('price 99999, quantity 84'),0.0)
        self.assertEqual(seller.required_food_hours('use within 12 h; min_hours_left=36'),36.0)

    def test_warranty_requirement_parser_is_explicit_and_bounded(self):
        self.assertEqual(seller.required_warranty_hours('Require 6-hour warranty.'),6.0)
        self.assertEqual(seller.required_warranty_hours('warranty_hours=12 h'),12.0)
        self.assertEqual(seller.required_warranty_hours('보증 6시간 이상'),6.0)
        self.assertEqual(seller.required_warranty_hours(
            'price 600000; warranty available'),0.0)

    def test_unspecified_food_condition_uses_safe_default_but_explicit_shorter_wins(self):
        now=time.time()
        short=dict(unit='short',sku='FOOD1',name='food',category='food',kind='food',grade='C',
                   expires_at=now+20*3600,supplied_at=1)
        long=dict(unit='long',sku='FOOD1',name='food',category='food',kind='food',grade='C',
                  expires_at=now+80*3600,supplied_at=2)
        self.assertEqual(seller.food_hours_requirement('I want FOOD1',long),48.0)
        self.assertEqual(seller.select_unit('I want FOOD1',[short,long],set(),{})['unit'],'long')
        self.assertIsNone(seller.select_unit('I want FOOD1',[short],set(),{}))
        self.assertEqual(seller.food_hours_requirement('use within 12 h',short),12.0)
        self.assertEqual(seller.select_unit('FOOD1; use within 12 h',[short],set(),{})['unit'],'short')

    def test_overstocked_exact_sku_leads_with_low_cost_b(self):
        units=[]
        for n,grade,cost in [(0,'A',170),(1,'B',140),(2,'B',120),(3,'C',90),
                             (4,'A',180),(5,'A',175),(6,'A',172),(7,'A',171)]:
            units.append(dict(unit=str(n),sku='SOFA',name='sofa',category='goods',kind='goods',
                              grade=grade,cost=cost,expires_at=None,supplied_at=n))
        self.assertEqual(seller.select_unit('SOFA',units,set(),{})['unit'],'2')
        self.assertEqual(seller.select_unit('SOFA',units,set(),{},prefer_c=True)['grade'],'C')

    def test_c_drain_slot_is_deterministic_and_pressure_bounded(self):
        units=[{'unit':str(i),'grade':'C' if i<5 else 'A'} for i in range(10)]
        first=seller.c_drain_slot('task-1',units,set())
        self.assertEqual(first,seller.c_drain_slot('task-1',units,set()))
        self.assertGreaterEqual(first[1],.03)
        self.assertLessEqual(first[1],.15)
        self.assertEqual(seller.c_drain_slot('task-1',[{'unit':'a','grade':'A'}],set()),(False,0.0))

    def test_stock_miss_distinguishes_reserved_without_logging_secrets(self):
        u=dict(unit='u',sku='G00001',name='item',category='food',grade='A',expires_at=None)
        catalog={'G00001':dict(category='food')}
        result=seller.stock_miss('G00001 member=SECRET ref=REF-PRIVATE',[u],{'u'},catalog)
        self.assertEqual(result['reason'],'reserved')
        self.assertEqual(result['skus'],['G00001'])
        self.assertNotIn('SECRET',json.dumps(result))
        self.assertNotIn('REF-PRIVATE',json.dumps(result))
        hidden=seller.stock_miss('food member=G00001',[],set(),catalog)
        self.assertEqual(hidden['skus'],[])
        missing=seller.stock_miss('food',[],set(),catalog)
        self.assertEqual(missing['categories'],['food'])
        self.assertEqual(missing['reason'],'unavailable_or_unmatched')

    async def test_a2a_quote_signed_artifact_without_new_referral_enrollment(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state/'inventory.json').write_text(json.dumps({'round':3, 'balance':5000000, 'start':5000000, 'units': [dict(
                unit='u1', sku='G00001', name='김치', category='김치', cost=4000, kind='goods',
                list_price=10000, grade='A', expires_at=None, supplied_at=time.time())]}))
            (state/'catalog.json').write_text(json.dumps({'G00001':{'kind':'food','shelf_hours':28}}))
            program = Program(state)
            program.put('cursor', 100)
            program.close()
            with patch.object(seller, 'STATE', state), patch.object(seller, 'sign', side_effect=lambda r: dict(r, sig_seller='test-only')):
                app = seller.build(9301, publish=False)
                # ASGI transport deliberately does not run lifespan/network worker.
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                    async def send(text, task=None):
                        msg = {'role':'ROLE_USER', 'messageId':str(time.time_ns()), 'parts':[{'text':text}]}
                        if task:
                            msg['taskId'] = task
                        response = await client.post('/', headers={'A2A-Version':'1.0'}, json={
                            'jsonrpc':'2.0','id':'1','method':'SendMessage','params':{'message':msg}})
                        self.assertEqual(response.status_code, 200)
                        data = response.json()
                        self.assertNotIn('error', data, data)
                        return data['result'].get('task', data['result'])
                    result = await send('I want to buy G00001')
                    task = result['id']
                    result = await send('9500', task)
                    self.assertIn('COMPLETED', result['status']['state'])
                    artifacts = json.dumps(result['artifacts'])
                    self.assertIn('sig_seller', artifacts)
                    self.assertIn('"claim": "A"',artifacts)
                    self.assertNotIn('member', artifacts)
                    self.assertNotIn('Private provisional member key', json.dumps(result))
                    self.assertNotIn('Public referral code: REF-',json.dumps(result))
                    discovery=await send('OFFERS')
                    self.assertIn('COMPLETED',discovery['status']['state'])
                    self.assertNotIn('Private provisional',json.dumps(discovery))
                    retired=await send('SHARE member=invalid')
                    self.assertIn('campaign has ended',json.dumps(retired))
                    program = Program(state)
                    self.assertEqual(program.db.execute('SELECT COUNT(*) FROM sales WHERE ledger IS NULL').fetchone()[0], 1)
                    self.assertEqual(program.db.execute('SELECT COUNT(*) FROM members').fetchone()[0], 0)
                    self.assertEqual(program.db.execute('SELECT COUNT(*) FROM members WHERE active=1').fetchone()[0], 0)
                    program.close()
                    with sqlite3.connect(state/'adaptive.sqlite3') as adaptive:
                        self.assertEqual(adaptive.execute(
                            "SELECT state FROM mutations WHERE role='seller'").fetchone()[0],'CONFIRMED')
                        self.assertEqual(adaptive.execute(
                            "SELECT COUNT(*) FROM runtime_events WHERE kind='sale_signed'").fetchone()[0],1)

    async def test_goods_negotiation_pins_warranty_and_reserves_refund_cash(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)
            (state/'inventory.json').write_text(json.dumps(dict(round=3,start=5000000,balance=5000000,units=[dict(
                unit='goods-u',sku='G99999',name='chair',category='chair',kind='goods',cost=6000,
                list_price=20000,grade='B',expires_at=None,supplied_at=time.time())])))
            program=Program(state);program.put('cursor',100);program.close()
            with patch.object(seller,'STATE',state),patch.object(seller,'sign',side_effect=lambda r:dict(r,sig_seller='test-only')):
                app=seller.build(9301,publish=False)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
                    async def send(text,task=None):
                        msg=dict(role='ROLE_USER',messageId=str(time.time_ns()),parts=[dict(text=text)])
                        if task:msg['taskId']=task
                        response=await c.post('/',headers={'A2A-Version':'1.0'},json=dict(jsonrpc='2.0',id='1',method='SendMessage',params=dict(message=msg)))
                        body=response.json()['result'];return body.get('task',body)
                    quote=await send('G99999 warranty 6 h')
                    self.assertIn('warranty 6 h',json.dumps(quote))
                    # Repeated messages on one inquiry must not become extra
                    # procurement demand or failed conversion trials.
                    for _ in range(5):
                        refreshed=await send('Please confirm the price',quote['id'])
                        self.assertIn('INPUT_REQUIRED',refreshed['status']['state'])
                    done=await send('15000',quote['id'])
                    self.assertIn('COMPLETED',done['status']['state'])
                    receipt=done['artifacts'][0]['parts'][0]['data']
                    self.assertEqual(receipt['warranty_hours'],6)
                    self.assertEqual(receipt['price'],15000)
                    self.assertEqual(receipt['claim'],'B')
                    with sqlite3.connect(state/'market_intel.sqlite3') as db:
                        self.assertEqual(db.execute(
                            "SELECT COUNT(*) FROM decisions WHERE role='seller_quote'").fetchone()[0],1)
                        signal=json.loads(db.execute(
                            "SELECT body FROM decisions WHERE role='seller_offer_received'").fetchone()[0])
                        self.assertEqual(signal['sku'],'G99999');self.assertEqual(signal['offered'],15000)
                        self.assertEqual(signal['offer_discount_pct'],25.0)
                    program=Program(state)
                    try:self.assertEqual(program.db.execute('select amount from warranties').fetchone()[0],15000)
                    finally:program.close()

    async def test_unverified_grade_never_reserves_or_issues_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)
            (state/'inventory.json').write_text(json.dumps(dict(round=3,start=5000000,balance=5000000,units=[dict(
                unit='goods-u',sku='G99999',name='chair',category='chair',kind='goods',cost=6000,
                list_price=20000,grade='B',expires_at=None,supplied_at=time.time())])))
            program=Program(state);program.put('cursor',100);program.close()
            with patch.object(seller,'STATE',state),patch.object(seller,'sign',side_effect=lambda r:dict(r,sig_seller='test-only')):
                app=seller.build(9301,publish=False)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
                    async def send(text,task=None):
                        msg=dict(role='ROLE_USER',messageId=str(time.time_ns()),parts=[dict(text=text)])
                        if task:msg['taskId']=task
                        response=await c.post('/',headers={'A2A-Version':'1.0'},json=dict(jsonrpc='2.0',id='1',method='SendMessage',params=dict(message=msg)))
                        body=response.json()['result'];return body.get('task',body)
                    quote=await send('G99999 warranty 6 h')
                    self.assertIn('warranty 6 h',json.dumps(quote))
                    with patch.object(seller, 'receipt_grade', return_value=None):
                        done=await send('15000',quote['id'])
                    self.assertIn('REJECTED', done['status']['state'])
                    self.assertNotIn('artifacts', done)
                    program=Program(state)
                    try:self.assertEqual(program.db.execute('select count(*) from sales').fetchone()[0],0)
                    finally:program.close()


if __name__ == '__main__':
    unittest.main()
