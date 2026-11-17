"""RED: seller bounded-v1 first vertical slice. No network, no model calls, no real key.

Assumed contract (initial assumptions, not optimal values):
- POLICY_VERSION == 'bounded-v1'
- first_quote(unit) = max(cost, list_price*9//10)
- next_quote(unit, ask) = max(cost, ask - max(1, list_price*5//100)); never above ask
- fallback_move(unit, ask, offer) -> ('counter', next_quote) or ('accept', offer) only if offer >= next_quote(unit, ask)
- model_price(unit, ask, price) -> strict int clamped to [cost, ask], or None (bool/float/str/non-finite)
- append-only $MARKET_DIR/decisions.jsonl rows; negotiation complete is never paid/settled; no raw buyer text
"""
import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import seller_r3

UNIT = dict(unit='v1-unit', sku='GPOLICY', name='Policy fixture', category='test', grade='A', kind='goods',
            cost=50, list_price=100, expires_at=None, supplied_at=1)
SECRET = 'RAW-BUYER-PHRASE-xyz'
SETTLEMENT_KEYS = {'paid', 'settled', 'ledger', 'settlement'}


def quote_of(task):
    return int(re.findall(r'\d+', task['status']['message']['parts'][0]['text'])[-1])


class PureHelperTests(unittest.TestCase):
    def test_policy_version(self):
        self.assertEqual(seller_r3.POLICY_VERSION, 'bounded-v2')

    def test_first_quote_and_stepwise_concession_to_cost(self):
        self.assertEqual(seller_r3.first_quote(UNIT), 90)
        self.assertEqual(seller_r3.first_quote(dict(UNIT, cost=95)), 95)
        self.assertEqual(seller_r3.first_quote(dict(UNIT, cost=1000, list_price=1999)), 1799)
        self.assertEqual(seller_r3.next_quote(dict(UNIT, cost=1000, list_price=1999), 1799), 1700)
        asks = [seller_r3.first_quote(UNIT)]
        for _ in range(9):
            asks.append(seller_r3.next_quote(UNIT, asks[-1]))
        self.assertEqual(asks, [90, 85, 80, 75, 70, 65, 60, 55, 50, 50])

    def test_fallback_accepts_only_at_or_above_next_threshold(self):
        self.assertEqual(seller_r3.fallback_move(UNIT, None, None), ('counter', 90))
        self.assertEqual(seller_r3.fallback_move(UNIT, 90, 60), ('counter', 85))   # >= cost is not enough
        self.assertEqual(seller_r3.fallback_move(UNIT, 90, 84), ('counter', 85))
        self.assertEqual(seller_r3.fallback_move(UNIT, 90, 86), ('accept', 86))
        self.assertEqual(seller_r3.fallback_move(UNIT, 50, 49), ('counter', 50))
        self.assertEqual(seller_r3.fallback_move(UNIT, 50, 50), ('accept', 50))

    def test_model_price_strict_int_never_raises_never_below_cost(self):
        mp = seller_r3.model_price
        self.assertEqual(mp(UNIT, None, 90), 90)
        self.assertEqual(mp(UNIT, 90, 120), 90)
        self.assertEqual(mp(UNIT, 90, 30), 50)
        for bad in (True, False, 70.0, 85.5, float('inf'), float('nan'), '70', None, [70]):
            self.assertIsNone(mp(UNIT, 90, bad), repr(bad))


class SellerJournalA2ATests(unittest.IsolatedAsyncioTestCase):
    async def exchange(self, tmp, first, offers, model=None):
        inv = Path(tmp, 'inventory.json')
        if not inv.exists():
            inv.write_text(json.dumps({'round': 3, 'units': [UNIT]}))
        signed, tasks = [], []
        ask = patch.object(seller_r3, 'ask_json', return_value=(None, '')) if model is None else \
            patch.object(seller_r3, 'ask_json', side_effect=[(m, 'fixture') for m in model])
        fake_sign = lambda rec: signed.append(dict(rec)) or dict(rec, sig_seller='offline-signature-placeholder')
        with patch.object(seller_r3, 'MARKET_DIR', Path(tmp)), ask, patch.object(seller_r3, 'sign', side_effect=fake_sign):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=seller_r3.build(9999))) as c:
                tid = None
                for text in [first] + offers:
                    msg = {'role': 'ROLE_USER', 'messageId': str(len(tasks)), 'parts': [{'text': text}]}
                    if tid:
                        msg['taskId'] = tid
                    r = await c.post('http://fixture/', headers={'A2A-Version': '1.0'},
                                     json={'jsonrpc': '2.0', 'id': '1', 'method': 'SendMessage', 'params': {'message': msg}})
                    t = r.json()['result']['task']
                    tid = t['id']
                    tasks.append(t)
        return tasks, signed

    def rows(self, tmp):
        raw = Path(tmp, 'decisions.jsonl').read_text(encoding='utf-8')
        self.assertTrue(raw.endswith('\n'))
        self.assertNotIn(SECRET, raw)
        self.assertNotIn('sig_seller', raw)
        self.assertNotIn('offline-signature', raw)
        rows = [json.loads(line) for line in raw.splitlines()]
        for row in rows:
            self.assertEqual(row['policy_version'], 'bounded-v2')
            self.assertNotIn(row['event'], SETTLEMENT_KEYS)
        return rows

    @patch.object(seller_r3, 'FASTPATH', False)   # model-specific: harness path

    async def test_fallback_concedes_to_cost_then_journals_first_and_final_counter(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks, signed = await self.exchange(tmp, f'{SECRET} I want to buy GPOLICY', ['10'] * 9 + ['50'])
            asks = [quote_of(t) for t in tasks[:-1]]
            self.assertEqual(asks, [90, 85, 80, 75, 70, 65, 60, 55, 50, 50])
            self.assertEqual(tasks[-1]['status']['state'], 'TASK_STATE_COMPLETED')
            tid = tasks[-1]['id']
            self.assertEqual(len(signed), 1)
            self.assertIs(type(signed[0]['price']), int)
            self.assertEqual((signed[0]['price'], signed[0]['task'], signed[0]['item'], signed[0]['unit']),
                             (50, tid, 'GPOLICY', 'v1-unit'))

            snapshot = hashlib.sha256(Path(tmp, 'inventory.json').read_bytes()).hexdigest()
            rows = self.rows(tmp)
            self.assertEqual([r['event'] for r in rows], ['first_quote'] + ['counter'] * 9 + ['negotiation_complete'])
            self.assertEqual([r['quote'] for r in rows[:-1]], asks)
            self.assertEqual([r.get('offer') for r in rows[1:]], [10] * 9 + [50])
            for r in rows:
                self.assertEqual((r['task'], r['unit'], r['sku'], r['cost'], r['list_price'], r['snapshot']),
                                 (tid, 'v1-unit', 'GPOLICY', 50, 100, snapshot))
            self.assertTrue(all(r['decided_by'] == 'fallback' for r in rows[:-1]))
            done = rows[-1]
            self.assertEqual((done['first_quote'], done['final_quote'], done['price']), (90, 50, 50))
            self.assertFalse(SETTLEMENT_KEYS & set(done))

    @patch.object(seller_r3, 'FASTPATH', False)   # model-specific: harness path

    async def test_model_quotes_are_strict_monotone_and_cost_bounded(self):
        model = [{'action': 'counter', 'price': 90}, {'action': 'counter', 'price': 120},
                 {'action': 'counter', 'price': True}, {'action': 'counter', 'price': 70.0},
                 {'action': 'counter', 'price': 30}, {'action': 'counter', 'price': float('nan')}]
        with tempfile.TemporaryDirectory() as tmp:
            tasks, _ = await self.exchange(tmp, 'I want to buy GPOLICY', ['10'] * 5, model)
            self.assertEqual([quote_of(t) for t in tasks], [90, 90, 85, 80, 50, 50])
            rows = self.rows(tmp)
            self.assertEqual([r['decided_by'] for r in rows], ['model', 'model', 'fallback', 'fallback', 'model', 'fallback'])

    @patch.object(seller_r3, 'FASTPATH', False)   # model-specific: harness path

    async def test_reject_and_no_stock_are_journaled_without_raw_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks, _ = await self.exchange(tmp, 'I want to buy GPOLICY', ['10'],
                                           [{'action': 'counter', 'price': 90}, {'action': 'reject'}])
            self.assertEqual(tasks[-1]['status']['state'], 'TASK_STATE_REJECTED')
            tasks, _ = await self.exchange(tmp, f'{SECRET} I want to buy NOTHINGHERE', [])
            self.assertEqual(tasks[-1]['status']['state'], 'TASK_STATE_REJECTED')
            rows = self.rows(tmp)
            self.assertEqual([r['event'] for r in rows], ['first_quote', 'reject', 'no_stock'])
            self.assertEqual((rows[1]['first_quote'], rows[1]['final_quote']), (90, 90))
            self.assertEqual(rows[2]['task'], tasks[-1]['id'])

    async def test_journal_failure_fails_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'decisions.jsonl').mkdir()       # unwritable journal path
            tasks, _ = await self.exchange(tmp, 'I want to buy GPOLICY', ['10'])
            self.assertEqual([quote_of(t) for t in tasks], [90, 85])
            self.assertEqual(tasks[-1]['status']['state'], 'TASK_STATE_INPUT_REQUIRED')


if __name__ == '__main__':
    unittest.main()
