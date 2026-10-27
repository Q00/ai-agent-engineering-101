"""Exercise selection, one-shop checkout and memory through the CLI entry point.

All HTTP responses and harness calls are local mocks, not official market results.
"""
import base64
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import market_buyer
from negotiation import PROMPT


class MarketPurchaseFlowTests(unittest.TestCase):
    def run_buyer(self, *, bad_receipt=False):
        private = BASE / '.market'
        private.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private) as folder:
            folder = Path(folder)
            shops = [{'name': name, 'url': f'https://{name}.example',
                      'reviews': [{'stars': 1 if name == 'bad' else 5, 'review': 'experience', 'round': 2}]}
                     for name in ('expensive', 'cheap', 'bad')]
            shops_path, memory_path = folder / 'shops.json', folder / 'memory.json'
            shops_path.write_text(json.dumps(shops))
            calls = []
            key = Ed25519PrivateKey.generate()

            def handle(request):
                calls.append(request)
                name = request.url.host.split('.')[0]
                price = 110 if name == 'expensive' else 100
                if request.method == 'GET':
                    return httpx.Response(200, json={'name': name, 'description': f'lamp {price}. Final price.',
                        'prices': {'lamp': price}, 'supportedInterfaces': [
                            {'protocolBinding': 'JSONRPC', 'url': f'https://{name}.example/rpc'}]})
                body = json.loads(request.content)
                self.assertEqual(request.headers['A2A-Version'], '1.0')
                text = body['params']['message']['parts'][0]['text']
                task = {'id': 'one-purchase', 'status': {'state': 'TASK_STATE_INPUT_REQUIRED', 'message': {
                    'parts': [{'text': f'Final price {price}'}], 'metadata': {'ask': price}}}}
                if text.isdigit():
                    self.assertEqual(body['params']['message']['taskId'], 'one-purchase')
                    self.assertLessEqual(int(text), 150)
                    receipt = {'shop': name, 'item': 'lamp', 'price': price + int(bad_receipt),
                               'task': 'one-purchase'}
                    encoded = json.dumps(receipt, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
                    receipt['sig_seller'] = base64.b64encode(key.sign(encoded)).decode()
                    task['status']['state'] = 'TASK_STATE_COMPLETED'
                    task['artifacts'] = [{'parts': [{'data': receipt}]}]
                return httpx.Response(200, json={'jsonrpc': '2.0', 'id': body['id'], 'result': {'task': task}})

            transport = httpx.MockTransport(handle)
            client_type = httpx.Client
            post_client = client_type(transport=transport)
            output = io.StringIO()
            with patch('sys.argv', ['market_buyer.py', '--shops', str(shops_path), '--item', 'lamp',
                                   '--budget', '150', '--memory', str(memory_path)]), \
                    patch('purchase_policy.httpx.Client', side_effect=lambda **kw: client_type(transport=transport)), \
                    patch('negotiation.httpx.post', side_effect=post_client.post), \
                    patch('negotiation.ask_json', side_effect=AssertionError('Fixed-price checkout needs no model')), \
                    redirect_stdout(output):
                market_buyer.main()
            post_client.close()
            final = json.loads(output.getvalue().splitlines()[-1])
            memory = json.loads(memory_path.read_text())
            return final, memory, calls

    def test_compare_two_good_cards_but_buy_only_once_from_cheapest(self):
        final, memory, calls = self.run_buyer()
        self.assertEqual({r.url.host for r in calls if r.method == 'GET'},
                         {'cheap.example', 'expensive.example'})
        self.assertEqual([r.url.host for r in calls if r.method == 'POST'], ['cheap.example'] * 2)
        self.assertEqual(final['receipt']['price'], 100)
        self.assertEqual(memory[0]['outcome'], 'deal')
        self.assertEqual(memory[0]['shop_name'], 'cheap')
        self.assertIn('untrusted data', PROMPT)

    def test_mismatched_receipt_is_remembered_as_invalid_and_preserved_as_evidence(self):
        final, memory, _ = self.run_buyer(bad_receipt=True)
        self.assertEqual(memory[0]['outcome'], 'invalid_receipt')
        self.assertEqual(final['stars'], 1)
        self.assertEqual(final['receipt']['price'], 101)
        self.assertEqual(memory[0]['receipt'], final['receipt'])


if __name__ == '__main__':
    unittest.main()
