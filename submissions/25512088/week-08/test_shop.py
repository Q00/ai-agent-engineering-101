"""Offline regressions for shop discovery and buyer demand accounting."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
import seller

from market_intel import Intel
from seller_ledger import Program
from seller_offers import offers
from seller_strategy import sale_quote


class ShopDiscoveryTests(unittest.TestCase):
    def test_discovery_does_not_inflate_inquiries_or_trigger_conversion_discount(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unit = dict(unit='u', sku='sku', name='chair', grade='B', kind='goods',
                        list_price=20000, cost=8000, expires_at=None)
            units = [dict(unit, unit=f'u{i}') for i in range(6)]
            (root/'inventory.json').write_text(json.dumps(dict(round=3, units=units)))
            intel = Intel(root)
            program = Program(root)
            try:
                intel.save_source('shops', {'shops': [dict(
                    name='rival', offers=[dict(sku='sku', qty=4)])]})
                quoted = sale_quote(unit, {}, program, intel)
                for _ in range(3):
                    preview = offers(root, program, intel, limit=1)
                    self.assertEqual(preview[0]['price'], quoted['price'])
                count = intel.db.execute(
                    "SELECT COUNT(*) FROM decisions WHERE role='seller_quote'").fetchone()[0]
                self.assertEqual(count, 1)
                # Actual buyer quotes still feed the existing demand accounting.
                next_quote = sale_quote(unit, {}, program, intel)
                self.assertEqual(next_quote['price'], quoted['price'])
                self.assertEqual(intel.db.execute(
                    "SELECT COUNT(*) FROM decisions WHERE role='seller_quote'").fetchone()[0], 2)
            finally:
                program.close()
                intel.close()


class ShopInquiryTests(unittest.IsolatedAsyncioTestCase):
    async def test_followups_do_not_create_demand_or_conversion_discounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unit = dict(unit='u', sku='sku', name='chair', grade='B', kind='goods',
                        list_price=20000, cost=8000, expires_at=None)
            (root/'inventory.json').write_text(json.dumps(dict(round=3, units=[unit])))
            (root/'catalog.json').write_text(json.dumps({
                'sku': dict(category='chairs', list_price=20000)}))
            program = Program(root)
            program.put('cursor', 100)
            program.close()
            intel = Intel(root)
            try:
                intel.save_source('shops', {'shops': [dict(
                    name='rival', offers=[dict(sku='sku', qty=4)])]})
                with patch.object(seller, 'STATE', root):
                    app = seller.build(9301, publish=False)
                    # No lifespan: the real request path runs without market workers.
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                                 base_url='http://test') as client:
                        async def send(text, task=None):
                            message = dict(role='ROLE_USER', messageId=text,
                                           parts=[dict(text=text)])
                            if task:
                                message['taskId'] = task
                            response = await client.post('/', headers={'A2A-Version': '1.0'},
                                json=dict(jsonrpc='2.0', id='1', method='SendMessage',
                                          params=dict(message=message)))
                            self.assertEqual(response.status_code, 200)
                            body = response.json()
                            self.assertNotIn('error', body)
                            result = body['result']
                            return result.get('task', result)

                        initial = await send('I want to buy sku')
                        for i in range(6):
                            followup = await send(f'Confirm the current price {i}', initial['id'])
                        count = intel.db.execute(
                            "SELECT COUNT(*) FROM decisions WHERE role='seller_quote'").fetchone()[0]
                        self.assertEqual(count, 1)
                        self.assertIn('Asking 16000', json.dumps(followup))
                        await send('I want to buy sku again')
                        self.assertEqual(intel.db.execute(
                            "SELECT COUNT(*) FROM decisions WHERE role='seller_quote'").fetchone()[0], 2)
            finally:
                intel.close()


if __name__ == '__main__':
    unittest.main()
