import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
import httpx
import referral_seller
from referral_program import Program


class SellerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def test_stock_miss_distinguishes_reserved_without_logging_secrets(self):
        u=dict(unit='u',sku='G00001',name='item',category='food',grade='A',expires_at=None)
        catalog={'G00001':dict(category='food')}
        result=referral_seller.stock_miss('G00001 member=SECRET ref=REF-PRIVATE',[u],{'u'},catalog)
        self.assertEqual(result['reason'],'reserved')
        self.assertEqual(result['skus'],['G00001'])
        self.assertNotIn('SECRET',json.dumps(result))
        self.assertNotIn('REF-PRIVATE',json.dumps(result))
        hidden=referral_seller.stock_miss('food member=G00001',[],set(),catalog)
        self.assertEqual(hidden['skus'],[])
        missing=referral_seller.stock_miss('food',[],set(),catalog)
        self.assertEqual(missing['categories'],['food'])
        self.assertEqual(missing['reason'],'unavailable_or_unmatched')

    async def test_a2a_quote_signed_artifact_and_private_enrollment(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state/'inventory.json').write_text(json.dumps({'round':3, 'balance':5000000, 'start':5000000, 'units': [dict(
                unit='u1', sku='G00001', name='김치', category='김치', cost=4000, kind='goods',
                list_price=10000, grade='A', expires_at=None, supplied_at=time.time())]}))
            (state/'catalog.json').write_text(json.dumps({'G00001':{'kind':'food','shelf_hours':28}}))
            program = Program(state)
            program.put('cursor', 100)
            program.close()
            with patch.object(referral_seller, 'STATE', state), patch.object(referral_seller, 'sign', side_effect=lambda r: dict(r, sig_seller='test-only')):
                app = referral_seller.build(9301, publish=False)
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
                    self.assertIn('Private provisional member key', json.dumps(result))
                    self.assertIn('Public referral code: REF-',json.dumps(result))
                    self.assertIn('SHARE member=',json.dumps(result))
                    discovery=await send('OFFERS')
                    self.assertIn('COMPLETED',discovery['status']['state'])
                    self.assertNotIn('Private provisional',json.dumps(discovery))
                    rejected=await send('SHARE member=invalid')
                    self.assertIn('requires your private member key',json.dumps(rejected))
                    program = Program(state)
                    self.assertEqual(program.db.execute('SELECT COUNT(*) FROM sales WHERE ledger IS NULL').fetchone()[0], 1)
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
            with patch.object(referral_seller,'STATE',state),patch.object(referral_seller,'sign',side_effect=lambda r:dict(r,sig_seller='test-only')):
                app=referral_seller.build(9301,publish=False)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
                    async def send(text,task=None):
                        msg=dict(role='ROLE_USER',messageId=str(time.time_ns()),parts=[dict(text=text)])
                        if task:msg['taskId']=task
                        response=await c.post('/',headers={'A2A-Version':'1.0'},json=dict(jsonrpc='2.0',id='1',method='SendMessage',params=dict(message=msg)))
                        body=response.json()['result'];return body.get('task',body)
                    quote=await send('G99999')
                    self.assertIn('warranty 6 h',json.dumps(quote))
                    done=await send('15000',quote['id'])
                    self.assertIn('COMPLETED',done['status']['state'])
                    receipt=done['artifacts'][0]['parts'][0]['data']
                    self.assertEqual(receipt['warranty_hours'],6)
                    self.assertEqual(receipt['price'],15000)
                    self.assertEqual(receipt['claim'],'B')
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
            with patch.object(referral_seller,'STATE',state),patch.object(referral_seller,'sign',side_effect=lambda r:dict(r,sig_seller='test-only')):
                app=referral_seller.build(9301,publish=False)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
                    async def send(text,task=None):
                        msg=dict(role='ROLE_USER',messageId=str(time.time_ns()),parts=[dict(text=text)])
                        if task:msg['taskId']=task
                        response=await c.post('/',headers={'A2A-Version':'1.0'},json=dict(jsonrpc='2.0',id='1',method='SendMessage',params=dict(message=msg)))
                        body=response.json()['result'];return body.get('task',body)
                    quote=await send('G99999')
                    self.assertIn('warranty 6 h',json.dumps(quote))
                    with patch.object(referral_seller, 'receipt_grade', return_value=None):
                        done=await send('15000',quote['id'])
                    self.assertIn('REJECTED', done['status']['state'])
                    self.assertNotIn('artifacts', done)
                    program=Program(state)
                    try:self.assertEqual(program.db.execute('select count(*) from sales').fetchone()[0],0)
                    finally:program.close()


if __name__ == '__main__':
    unittest.main()
