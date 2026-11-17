"""Seller honest metadata slice. No network, no model calls, no real key; fixtures live in a per-test temp dir.

Assumed contract (initial assumptions, not optimal values):
- the receipt the seller sends over A2A carries cert=True (the kit attaches the market's unit certificate),
  warranty_hours=0 (explicit default off; the kit forwards only positive values, so no obligation), and for goods
  claim = the unit's grade (A/B/C) as read from inventory at accept time, not the grade cached at the first quote
- food omits claim (snapshot lag ~10s and grade aging) and never carries a 24h warranty
- inventory is re-read after the model call, before signing: a unit gone or expired meanwhile is rejected unsigned
- the kit reads claim/cert/warranty_hours from the seller's artifact (market_kit.py AcpShop.create), not under
  sig_seller; the signed body stays shop, item, price, task

Status: the goods-grade test was RED (terms-red1: None != 'B') and is the target of the terms implementation.
The removed-during-call, expires-during-call and food-terms tests were written after that implementation and are
regression guards: they may pass on first run, which is expected, not proof of a new behaviour.
The market-anchor test is RED for the next slice: seller_r3 does not call price_anchor yet.
"""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import seller_r3


def goods(uid, grade):
    return dict(unit=uid, sku='GTERMS', name='Terms fixture lamp', category='goods>lamp', grade=grade, kind='goods',
                cost=50, list_price=100, expires_at=None, supplied_at=1)


def food(uid, grade, expires_at):
    return dict(unit=uid, sku='FTERMS', name='Terms fixture kimchi', category='food>kimchi', grade=grade, kind='food',
                cost=50, list_price=100, expires_at=expires_at, supplied_at=1)


def packet(anchor):
    """Shape of public_evidence.price_anchor's return value; values are fixtures."""
    return {'anchor': anchor, 'reason': f'fixture anchor {anchor}', 'ledger_ids': [1, 2, 3], 'sample_count': 3,
            'comparable_count': 3, 'computed_at': 1000, 'version': 'price-anchor-v1', 'stale': False,
            'limitations': ['fixture']}


def quote_of(task):
    return int(re.findall(r'\d+', task['status']['message']['parts'][0]['text'])[-1])


class SellerTermsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.signed = []
        self.clock = 1000
        fake_sign = lambda rec: self.signed.append(dict(rec)) or dict(rec, sig_seller='offline-signature-placeholder')
        model = [({'action': 'counter', 'price': 90}, 'fixture'), ({'action': 'accept'}, 'fixture')]
        self.patches = [patch.object(seller_r3, 'MARKET_DIR', self.tmp),
                        patch.object(seller_r3, 'ask_json', side_effect=model),   # accept after the first counter
                        patch.object(seller_r3, 'sign', side_effect=fake_sign),
                        patch.object(seller_r3, 'now', side_effect=lambda: self.clock)]
        self.ask = [p.start() for p in self.patches][1]
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=seller_r3.build(9999)))

    async def asyncTearDown(self):
        await self.client.aclose()
        for p in reversed(self.patches):
            p.stop()
        self._tmp.cleanup()

    def stock(self, *units):
        Path(self.tmp, 'inventory.json').write_text(json.dumps({'round': 3, 'units': list(units)}), encoding='utf-8')

    async def send(self, text, tid=None):
        msg = {'role': 'ROLE_USER', 'messageId': f'm-{text}-{tid}', 'parts': [{'text': text}]}
        if tid:
            msg['taskId'] = tid
        r = await self.client.post('http://fixture/', headers={'A2A-Version': '1.0'},
                                   json={'jsonrpc': '2.0', 'id': '1', 'method': 'SendMessage', 'params': {'message': msg}})
        body = r.json()
        self.assertNotIn('error', body, body)
        return body['result']['task']

    def assert_rejected_unsigned(self, task):
        self.assertEqual(task['status']['state'], 'TASK_STATE_REJECTED')
        self.assertEqual(self.signed, [])
        self.assertFalse(task.get('artifacts'))

    async def test_goods_receipt_claims_fresh_grade_with_cert_and_no_warranty(self):
        self.stock(goods('t1', 'A'))
        a = await self.send('I want to buy GTERMS')
        self.assertEqual(a['status']['state'], 'TASK_STATE_INPUT_REQUIRED')

        self.stock(goods('t1', 'B'))                    # the unit aged to B before the buyer's offer was accepted
        done = await self.send('90', a['id'])
        self.assertEqual(done['status']['state'], 'TASK_STATE_COMPLETED')
        self.assertEqual(len(self.signed), 1)
        receipt = done['artifacts'][0]['parts'][0]['data']
        self.assertEqual((receipt['item'], receipt['unit'], receipt['price'], receipt['task']),
                         ('GTERMS', 't1', 90, a['id']))
        self.assertEqual(receipt.get('claim'), 'B')
        self.assertIs(receipt.get('cert'), True)
        self.assertEqual(receipt.get('warranty_hours'), 0)

    @patch.object(seller_r3, 'FASTPATH', False)   # model-specific: harness path

    async def test_unit_removed_during_model_call_is_rejected_unsigned(self):
        self.stock(goods('t1', 'A'))
        a = await self.send('I want to buy GTERMS')

        def sold_elsewhere(*args, **kwargs):
            self.stock()                                 # the unit leaves inventory while the model is thinking
            return {'action': 'accept'}, 'fixture'
        self.ask.side_effect = sold_elsewhere
        self.assert_rejected_unsigned(await self.send('90', a['id']))

    @patch.object(seller_r3, 'FASTPATH', False)   # model-specific: harness path

    async def test_food_expiring_during_model_call_is_rejected_unsigned(self):
        self.stock(food('f1', 'A', 1500))
        a = await self.send('I want to buy FTERMS')

        def slow_model(*args, **kwargs):
            self.clock = 2000                            # the call outlives expires_at=1500; inventory unchanged
            return {'action': 'accept'}, 'fixture'
        self.ask.side_effect = slow_model
        self.assert_rejected_unsigned(await self.send('90', a['id']))

    async def test_food_receipt_omits_claim_keeps_cert_and_no_24h_warranty(self):
        self.stock(food('f1', 'A', 5000))
        a = await self.send('I want to buy FTERMS')
        done = await self.send('90', a['id'])
        self.assertEqual(done['status']['state'], 'TASK_STATE_COMPLETED')
        receipt = done['artifacts'][0]['parts'][0]['data']
        self.assertEqual((receipt['item'], receipt['unit'], receipt['price']), ('FTERMS', 'f1', 90))
        self.assertNotIn('claim', receipt)
        self.assertNotIn('claim', self.signed[0])
        self.assertIs(receipt.get('cert'), True)
        self.assertEqual(receipt.get('warranty_hours'), 0)

    async def test_market_anchor_caps_model_opening_and_applies_only_to_new_tasks(self):
        """RED (next slice): the anchor caps the model's opening; a newer anchor never raises an open task's ask."""
        self.stock(goods('t1', 'A'), goods('t2', 'A'))
        self.ask.side_effect = [({'action': 'counter', 'price': 95}, 'fixture'),
                                ({'action': 'counter', 'price': 95}, 'fixture'),
                                ({'action': 'counter', 'price': 120}, 'fixture')]
        anchor = {'now': packet(70)}
        with patch.object(seller_r3, 'price_anchor', create=True, side_effect=lambda *a, **k: dict(anchor['now'])):
            a = await self.send('I want to buy GTERMS')
            self.assertEqual(quote_of(a), 70)            # model opened at 95; the anchor caps the first quote

            anchor['now'] = packet(99)                   # a newer anchor arrives between buyer turns
            b = await self.send('60', a['id'])
            self.assertEqual(b['status']['state'], 'TASK_STATE_INPUT_REQUIRED')
            self.assertLessEqual(quote_of(b), 70)        # the open task's quote never increases

            c = await self.send('I want to buy GTERMS')  # a new task gets the new policy
            self.assertNotEqual(c['id'], a['id'])
            self.assertEqual(quote_of(c), 99)


if __name__ == '__main__':
    unittest.main()
