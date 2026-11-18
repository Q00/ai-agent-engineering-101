import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
import buyer_r3
import seller_r3
from round3 import restock_hook
from signing import verify_receipt
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


class SellerExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.patch=patch.object(seller_r3,'MARKET_DIR',Path(self.temp.name))
        self.patch.start()
    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()
    async def test_matching_offer_and_reservation(self):
        seller=seller_r3.Seller()
        unit={'unit':'u','sku':'x','name':'X','kind':'goods','cost':50,'list_price':100}
        seller.open['task']={'unit':unit,'ask':100,'offer':None,'lines':[]}
        context=SimpleNamespace(current_task=SimpleNamespace(id='task',context_id='c'),message=None)
        updater=MagicMock()
        updater.add_artifact=AsyncMock()
        updater.complete=AsyncMock()
        with patch.object(seller_r3,'TaskUpdater',return_value=updater), \
             patch.object(seller_r3,'get_message_text',return_value='100'), \
             patch('harness.decide',new=AsyncMock(side_effect=AssertionError('model forbidden'))), \
             patch.object(seller_r3,'sign',side_effect=lambda r:r):
            await seller.execute(context,None)
        updater.complete.assert_awaited_once()
        self.assertIn('u',seller.promised)
        self.assertNotIn('task',seller.open)

    async def test_no_accepted_price_inferred_from_unrelated_number(self):
        seller=seller_r3.Seller()
        unit={'unit':'u','sku':'x','name':'X','kind':'goods','cost':50,'list_price':100}
        seller.open['task']={'unit':unit,'ask':100,'offer':None,'lines':[]}
        context=SimpleNamespace(current_task=SimpleNamespace(id='task',context_id='c'),message=None)
        updater=MagicMock()
        updater.requires_input=AsyncMock()
        with patch.object(seller_r3,'TaskUpdater',return_value=updater), \
             patch.object(seller_r3,'get_message_text',return_value='delivery takes 100 days'), \
             patch.object(seller_r3,'read_price',new=AsyncMock(return_value=None)), \
             patch('harness.decide',new=AsyncMock(side_effect=AssertionError('model forbidden'))):
            await seller.execute(context,None)
        updater.requires_input.assert_awaited_once()
        self.assertFalse(seller.promised)

class ContractTests(unittest.TestCase):
    def test_buyer_acceptable_stock_budget(self):
        shops=[{'url':'http://shop','offers':[{'sku':'x','qty':1},{'sku':'y','qty':1}]}]
        order={'acceptable':['x'],'budget':60}
        rows=buyer_r3.candidates(shops,order,{'x':{'list_price':100}})
        self.assertEqual([(r[2],r[3]) for r in rows],[('x',60)])

    def test_seller_signs_canonical_body_and_retains_unit(self):
        with tempfile.TemporaryDirectory() as d:
            key=Ed25519PrivateKey.generate()
            keypath=Path(d)/'key.pem'
            keypath.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            real_path=Path
            with patch.object(seller_r3,'Path',side_effect=lambda p:keypath if p=='shop_key.pem' else real_path(p)):
                rec=seller_r3.sign({'shop':'s','item':'x','price':10,'task':'t','unit':'u','cert':True})
            public=base64.b64encode(key.public_key().public_bytes_raw()).decode()
            verify_receipt(rec,public)
            self.assertEqual(rec['unit'],'u')
            self.assertTrue(rec['cert'])

    def test_restock_contract_drafts_no_publish(self):
        with tempfile.TemporaryDirectory() as d:
            md=Path(d)
            inv={'round':3,'balance':1000000,'restock':{'open':True},'units':[
                {'unit':'u','sku':'x','name':'X','kind':'goods','cost':50,'list_price':100}]}
            (md/'inventory.json').write_text(json.dumps(inv))
            (md/'catalog.json').write_text(json.dumps({'x':{'kind':'goods','list_price':100}}))
            result=restock_hook(md,10000)
            self.assertEqual(result,{'buy':[]})
            self.assertFalse((md/'outbox.jsonl').exists())
            self.assertFalse((md/'forum-drafts.json').exists())

    def test_a2a_app_build(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(seller_r3,'MARKET_DIR',Path(directory)):
            self.assertTrue(seller_r3.build(9301).routes)

if __name__=='__main__':
    unittest.main()
