"""Offline unit checks. Does not join the market or count as real settlement."""
from __future__ import annotations
import ast
import json
import os
import tempfile
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

# An inert placeholder allows importing the existing Groq harness without an API call.
os.environ.setdefault('OPENAI_API_KEY', 'offline-test-placeholder')
# Test logic without installing a provider client or issuing model calls.
openai_stub = types.ModuleType('openai')
openai_stub.OpenAI = lambda *args, **kwargs: object()
sys.modules['openai'] = openai_stub

from round3_inventory import find_stock_unit, selection_from_stock, unit_still_available
from restock import pick
import market_buyer as mb


class OfflineRound3Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / '.market').mkdir()
        self.prev = Path.cwd()
        os.chdir(self.root)
        self.oldmarket = os.environ.get('MARKET_DIR')
        os.environ['MARKET_DIR'] = str(self.root / '.market')
        mb.MEMORY_PATH = self.root / '.market' / 'buyer-memory.json'

    def tearDown(self):
        os.chdir(self.prev)
        if self.oldmarket is None: os.environ.pop('MARKET_DIR', None)
        else: os.environ['MARKET_DIR'] = self.oldmarket
        self.tmp.cleanup()

    def set_market(self, open=True):
        inv = {'round': 3, 'balance': 5_000_000, 'restock': {'open': open}, 'units': [
            {'unit':'u1','sku':'G001','name':'Test desk','category':'furniture>desk',
             'cost':6000,'list_price':10000,'grade':'A','kind':'goods','supplied_at':1, 'expires_at':None},
        ]}
        (self.root / '.market' / 'inventory.json').write_text(json.dumps(inv))
        catalog={'G001':{'category':'furniture>desk','name':'Test desk','list_price':10000,'kind':'goods'},
                 'G002':{'category':'electronics>lamp','name':'Test lamp','list_price':12000,'kind':'goods'}}
        (self.root / '.market' / 'catalog.json').write_text(json.dumps(catalog))
        return inv

    def test_inventory_selects_exact_sku(self):
        self.set_market()
        u = find_stock_unit('I want Test desk (G001)')
        self.assertEqual(u['unit'],'u1')
        p=selection_from_stock('I want Test desk (G001)')
        self.assertEqual(p['item'],'G001')
        self.assertEqual(p['reserve'],6300)
        self.assertTrue(unit_still_available('u1'))
        self.assertIsNone(find_stock_unit('G002'))
        self.assertIsNone(find_stock_unit('G001', ['u1']))

    def test_market_buyer_only_valid_stock(self):
        shops=[{'name':'Shop A','url':'https://example.invalid/a','offers':[{'sku':'G001','name':'Test desk','qty':1}]},
               {'name':'Shop B','url':'https://example.invalid/b','offers':[{'sku':'G002','name':'Test lamp','qty':1}]}]
        path=self.root / '.market' / 'shops.json'
        path.write_text(json.dumps(shops))
        loaded=mb.load_shops(str(path))
        self.assertEqual(mb.stocked_offers(loaded[0], {'G001'}),shops[0]['offers'])
        self.assertEqual(mb.stocked_offers(loaded[1], {'G001'}),[])

    def test_food_certificate_accept(self):
        policy=mb.acceptance_policy({'acceptable':['F001'],'kind':'food','use_within_hours':4})
        self.assertEqual(policy, {'cert':True,'min_hours_left':4.0})
        self.assertEqual(mb.acceptance_policy({'acceptable':['G001'],'kind':'goods'}),{'cert':True})

    def test_purchase_selection_from_order(self):
        self.set_market()
        shops=[{'name':'Shop A','url':'https://example.invalid/a','reviews':[],
                'offers':[{'sku':'G001','name':'Test desk','qty':2}]},
               {'name':'Shop B','url':'https://example.invalid/b','reviews':[],
                'offers':[{'sku':'G002','name':'Test lamp','qty':2}]}]
        sp=self.root / '.market' / 'shops.json';sp.write_text(json.dumps(shops))
        op=self.root / '.market' / 'order.json';op.write_text(json.dumps({'order':'abc','item':'desk','kind':'goods','acceptable':['G001']}))
        receipt={'item':'G001','price':8000,'task':'t1','shop':'Shop A','sig_seller':'test'}
        with patch.object(mb, 'negotiate',return_value=(receipt,'deal','ok')) as nego:
            answer=mb.run_market_buyer(str(sp),'desk',9000,str(op))
        self.assertEqual(answer['shop'],shops[0]['url'])
        self.assertEqual(answer['accept'],{'cert':True})
        self.assertEqual(nego.call_args.kwargs['requested_offer']['sku'],'G001')

    def test_no_stock_returns_no_deal(self):
        sp=self.root / '.market' / 'shops.json'
        sp.write_text(json.dumps([{'name':'Shop A','url':'https://example.invalid/a','reviews':[], 'offers':[]}]))
        op=self.root / '.market' / 'order.json';op.write_text(json.dumps({'order':'abc','item':'desk','acceptable':['G001']}))
        answer=mb.run_market_buyer(str(sp),'desk',9000,str(op))
        self.assertIsNone(answer['receipt'])

    def test_receipt_allows_category_or_acceptable_sku(self):
        shop={'name':'Shop A','url':'https://example.invalid/a'}
        for sku, expected in [('G001','deal'),('desk','deal'),('G004','invalid_receipt')]:
            receipt={'item':sku,'price':8000,'task':'t1','shop':'Shop A','sig_seller':'test'}
            task={'id':'t1','status':{'state':'TASK_STATE_COMPLETED'},'artifacts':[{'parts':[{'data':receipt}]}]}
            with patch.object(mb,'fetch_agent_card',return_value={}),\
                 patch.object(mb,'get_jsonrpc_endpoint',return_value='https://example.invalid/rpc'),\
                 patch.object(mb,'get_task',side_effect=lambda x:x),\
                 patch.object(mb,'send_message',return_value=task):
                out=mb.negotiate(shop,'desk',9000,acceptable={'G001'},requested_offer={'sku':'G001','name':'Test desk'})
            self.assertEqual(out[1],expected)

    def test_receipt_over_budget_rejected(self):
        shop={'name':'Shop A','url':'https://example.invalid/a'}
        receipt={'item':'G001','price':10001,'task':'t1','shop':'Shop A','sig_seller':'test'}
        task={'id':'t1','status':{'state':'TASK_STATE_COMPLETED'},'artifacts':[{'parts':[{'data':receipt}]}]}
        with patch.object(mb,'fetch_agent_card',return_value={}),\
             patch.object(mb,'get_jsonrpc_endpoint',return_value='https://example.invalid/rpc'),\
             patch.object(mb,'get_task',side_effect=lambda x:x),\
             patch.object(mb,'send_message',return_value=task):
            out=mb.negotiate(shop,'desk',9000,acceptable={'G001'},requested_offer={'sku':'G001','name':'Test desk'})
        self.assertEqual(out[1],'over_budget')

    def test_signed_receipt_canonical_four_fields(self):
        """Actual Ed25519 cryptographic round-trip, with a throwaway local key."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        import base64
        code = Path(__file__).with_name('seller.py').read_text(encoding='utf-8')
        tree = ast.parse(code)
        methods = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and node.name in ('canon','sign_receipt')]
        namespace = {'json':json,'base64':base64,'serialization':serialization,'Any':object}
        exec(compile(ast.Module(body=methods, type_ignores=[]), '<seller-sign>', 'exec'), namespace)
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(encoding=serialization.Encoding.PEM,
                                format=serialization.PrivateFormat.PKCS8,
                                encryption_algorithm=serialization.NoEncryption())
        path=self.root / 'temp_signing_key.pem'
        path.write_bytes(pem)
        receipt=namespace['sign_receipt']({'shop':'Arun A2A Seller','item':'G001',
                                          'price':9000,'task':'t1','unit':'u1'},str(path))
        body={k:receipt[k] for k in ('shop','item','price','task')}
        key.public_key().verify(base64.b64decode(receipt['sig_seller']), namespace['canon'](body))
        self.assertEqual(receipt['unit'],'u1')

    def test_restock_window_controls_buying(self):
        self.set_market(open=False)
        self.assertEqual(pick(self.root / '.market'),[])
        self.set_market(open=True)
        buy=pick(self.root / '.market')
        self.assertTrue(all(1<=x['qty']<=20 for x in buy))
        self.assertTrue(all(x['sku'] in ['G001','G002'] for x in buy))

if __name__ == '__main__':
    unittest.main(verbosity=2)
