"""Migration safety contract; subprocess tests never call the market or a model."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent

class SafetyTests(unittest.TestCase):
    def test_restock_cli_never_buys_or_files_bankruptcy(self):
        with tempfile.TemporaryDirectory() as state:
            Path(state, 'inventory.json').write_text(json.dumps({'balance': 0, 'units': []}))
            result = subprocess.run([sys.executable, str(ROOT / 'restock_guard.py'), '--state', state], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout.splitlines()[-1]), {'buy': []})

    def test_buyer_abstains_with_food_constraints_without_network(self):
        with tempfile.TemporaryDirectory() as state:
            order = Path(state, 'order.json')
            shops = Path(state, 'shops.json')
            order.write_text(json.dumps({'kind': 'food', 'acceptable': ['G00001'], 'use_within_hours': 7}))
            shops.write_text(json.dumps([{'url': 'http://127.0.0.1:1', 'offers': [{'sku': 'G00001', 'qty': 2}]}]))
            result = subprocess.run([sys.executable, str(ROOT / 'buyer_r3_guard.py'), '--shops', str(shops), '--order', str(order), '--item', 'food', '--budget', '100'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout.splitlines()[-1])
            self.assertIsNone(data['receipt'])
            self.assertEqual(data['accept'], {'cert': True, 'min_hours_left': 7, 'grades': ['A', 'B']})
            self.assertIn('awaiting user approval', data['review'])

    def test_stock_seller_fails_closed_without_inventory_and_selects_sku(self):
        code = '''
import json, tempfile
from pathlib import Path
import seller_r3 as seller
with tempfile.TemporaryDirectory() as tmp:
    seller.MARKET_DIR = Path(tmp)
    assert seller.find_unit('lamp') is None
    units = [dict(unit='u1',sku='G1',name='Kimchi',category='food',grade='A',cost=50,list_price=100,expires_at=9999999999,supplied_at=1)]
    Path(tmp, 'inventory.json').write_text(json.dumps({'round':3,'units':units}))
    assert seller.find_unit('G1')['unit'] == 'u1'
    assert seller.find_unit('G1', {'u1'}) is None
    units[0]['grade'] = 'expired'
    Path(tmp, 'inventory.json').write_text(json.dumps({'round':3,'units':units}))
    assert seller.find_unit('G1') is None
print('stock guard OK')
'''
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_starter_harness_uses_existing_direct_api_and_fails_closed(self):
        code = '''
from unittest.mock import patch
import harness
with patch('harness.complete', return_value='{"action":"counter","price":100}') as call:
    assert harness.ask_json('data')[0] == {'action':'counter','price':100}
    call.assert_called_once_with('data', model='gpt-6.1-terra')  # slot-recovery-v1: seller model exact
for raw in ['', '[]', 'not json']:
    with patch('harness.complete', return_value=raw):
        assert harness.ask_json('data')[0] is None
'''
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_deployment_config_disables_legacy_trading_and_default_restock(self):
        agent = json.loads((ROOT / 'agent.json').read_text())
        self.assertEqual(agent['restock']['cmd'], 'python restock_r3.py --state {state}')
        self.assertIn('seller_r3.py', agent['seller']['cmd'])
        self.assertIn('buyer_r3.py', agent['market_buyer']['cmd'])
        self.assertIn('--order {order}', agent['market_buyer']['cmd'])
        self.assertIn('buyer_r3.py', agent['buyer']['cmd'])
        self.assertEqual(agent['review']['cmd'], 'python review_r3.py --deal {deal}')
        self.assertEqual((ROOT / 'seller.py').read_bytes(), (ROOT / 'seller.week07.original.py').read_bytes())
        self.assertIn('*.pem', (ROOT / '.gitignore').read_text())
        self.assertIn('.env', (ROOT / '.gitignore').read_text())

    def test_launcher_uses_week08_without_changing_public_url_mode(self):
        launcher = Path('C:/Users/qkrgk/agent-market/rejoin.ps1').read_text()
        self.assertTrue('submissions/26512071/week-08' in launcher, 'Launcher must use week08 (content withheld)')
        self.assertTrue('"--public-url"' in launcher, 'Public URL mode required')
        self.assertTrue('$Port = 8900' in launcher, 'Port 8900 required')

class SellerSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_in_stock_negotiation_guard_and_signed_receipt_without_payment(self):
        import base64
        import os
        import httpx
        import seller_r3 as seller
        from unittest.mock import patch
        from cryptography.hazmat.primitives import serialization
        unit = dict(unit='local-test-unit', sku='GLOCAL', name='Local fixture', category='fixture',
                    grade='A', cost=50, list_price=100, expires_at=None, supplied_at=1)
        original_cwd = os.getcwd()
        try:
            os.chdir(ROOT)
            with tempfile.TemporaryDirectory() as tmp:
                Path(tmp, 'inventory.json').write_text(json.dumps({'round': 3, 'units': [unit]}))
                with patch.object(seller, 'MARKET_DIR', Path(tmp)), patch.object(seller, 'ask_json', side_effect=[
                    ({'action': 'counter', 'price': 1}, 'fixture'),
                    ({'action': 'accept'}, 'fixture'), ({'action': 'accept'}, 'fixture')]):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=seller.build(9999)), base_url='http://test') as client:
                        task = None
                        for index, text in enumerate(['GLOCAL', '40', '60']):
                            message = {'role': 'ROLE_USER', 'messageId': str(index), 'parts': [{'text': text}]}
                            if task:
                                message['taskId'] = task['id']
                            response = await client.post('/', headers={'A2A-Version':'1.0'}, json={'jsonrpc':'2.0','id':str(index),'method':'SendMessage','params':{'message':message}})
                            task = response.json()['result']['task']
                            if index == 0:
                                self.assertIn('50', task['status']['message']['parts'][0]['text'])
                            if index == 1:
                                self.assertNotIn('COMPLETED', task['status']['state'])
                        self.assertEqual(task['status']['state'], 'TASK_STATE_COMPLETED')
                        receipt = task['artifacts'][0]['parts'][0]['data']
                        # Protobuf Struct uses a regular object through the JSON codec.
                        self.assertEqual(receipt['item'], 'GLOCAL')
                        self.assertEqual(receipt['price'], 60)
                        self.assertEqual(receipt['unit'], 'local-test-unit')
                        key = serialization.load_pem_private_key((ROOT / 'shop_key.pem').read_bytes(), password=None)
                        # Match the actual invoked kit buyer path (:736): restore integral wire floats before verification.
                        signed = {k:receipt[k] for k in ('shop','item','price','task')}
                        self.assertTrue(float(signed['price']).is_integer())
                        signed['price'] = int(signed['price'])
                        key.public_key().verify(base64.b64decode(receipt['sig_seller']), seller.canon(signed))
        finally:
            os.chdir(original_cwd)

if __name__ == '__main__':
    unittest.main()
