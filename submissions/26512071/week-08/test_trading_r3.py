"""Offline operational safety fixtures: no market payments or model requests."""
import json
import tempfile
import unittest
from pathlib import Path

class RestockTests(unittest.TestCase):
    def test_small_diverse_exploration_reserves_capital_and_pending(self):
        import restock_r3
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            inv = {'round':3,'balance':5000000,'start':5000000,'units':[]}
            catalog = {f'G{i}':dict(name=f'Fixture {i}',category=f'cat{i%4}',kind='goods',list_price=10000) for i in range(10)}
            for name, data in [('inventory',inv),('catalog',catalog)]:
                (p/f'{name}.json').write_text(json.dumps(data))
            first = restock_r3.plan(p, now=100)
            self.assertGreater(len(first['buy']),0)
            self.assertLessEqual(len(first['buy']),3)
            self.assertEqual(len({catalog[x['sku']]['category'] for x in first['buy']}),len(first['buy']))
            self.assertTrue(all(x['qty']==1 for x in first['buy']))
            second = restock_r3.plan(p, now=110)
            self.assertFalse(set(x['sku'] for x in first['buy']) & set(x['sku'] for x in second['buy']))
            self.assertEqual(restock_r3.plan(p,now=120),{'buy':[]})
            inv['balance']=4000000
            (p/'inventory.json').write_text(json.dumps(inv))
            self.assertEqual(restock_r3.plan(p,now=500),{'buy':[]})

class BuyerTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_a2a_sku_budget_task_signature_and_food_policy(self):
        import base64
        import os
        import httpx
        import buyer_r3
        import seller_r3 as seller
        from unittest.mock import patch
        from cryptography.hazmat.primitives import serialization
        root=Path(__file__).resolve().parent
        old=os.getcwd()
        try:
            os.chdir(root)
            key=serialization.load_pem_private_key((root/'shop_key.pem').read_bytes(),None)
            pub=base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()
            with tempfile.TemporaryDirectory() as tmp:
                unit=dict(unit='fixture-unit',sku='GLOCAL',name='Fixture 123',category='food',kind='food',grade='A',cost=50,list_price=100,expires_at=9999999999,supplied_at=1)
                Path(tmp,'inventory.json').write_text(json.dumps({'round':3,'units':[unit]}))
                order={'acceptable':['GLOCAL'],'kind':'food','use_within_hours':7}
                shops=[{'name':'fixture','url':'http://test','offers':[{'sku':'BAD','qty':1},{'sku':'GLOCAL','name':'Fixture 123','qty':1}]}]
                self.assertEqual(buyer_r3.candidates(shops,order)[0][1],'GLOCAL')
                with patch.object(seller,'MARKET_DIR',Path(tmp)),patch.object(seller,'ask_json',side_effect=[({'action':'counter','price':80},'fixture'),({'action':'accept'},'fixture')]):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=seller.build(9999))) as client:
                        result=await buyer_r3.negotiate(client,shops[0],'GLOCAL',90,pub,order)
                self.assertEqual(result['receipt']['item'],'GLOCAL')
                self.assertLessEqual(result['receipt']['price'],90)
                self.assertEqual(result['accept'],{'cert':True,'grades':['A','B'],'min_hours_left':7})
                rec=result['receipt']
                self.assertIs(type(rec['price']),int)  # kit contract: int canonical (market_kit.py:736)
                self.assertEqual(buyer_r3.valid_receipt(dict(rec,price=float(rec['price'])),pub,'GLOCAL',rec['task'],90,80),rec)
                self.assertFalse(buyer_r3.valid_receipt(dict(rec,price=rec['price']-.5),pub,'GLOCAL',rec['task'],90,80))
                self.assertFalse(buyer_r3.valid_receipt(dict(rec,price=91),pub,'GLOCAL',rec['task'],90,80))
                self.assertFalse(buyer_r3.valid_receipt(dict(rec,task='wrong'),pub,'GLOCAL',rec['task'],90,80))
                self.assertFalse(buyer_r3.valid_receipt(dict(rec,sig_seller='bad'),pub,'GLOCAL',rec['task'],90,80))
                self.assertEqual(buyer_r3.policy({'kind':'goods'})['warranty_hours'],0)
        finally: os.chdir(old)

class ReviewTests(unittest.TestCase):
    def test_review_uses_actual_settled_quality_not_seller_praise(self):
        import review_r3
        self.assertEqual(review_r3.review({})['stars'],3)
        self.assertEqual(review_r3.review({'settled':{'grade':'B','claim_honest':True,'sku':'G1','ledger':42}})['stars'],4)
        self.assertEqual(review_r3.review({'settled':{'grade':'A','claim_honest':False,'sku':'G1','ledger':42}})['stars'],1)

if __name__ == '__main__': unittest.main()
