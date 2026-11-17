"""RED: seller unit lifecycle slice. No network, no model calls, no real key; fixtures live in a per-test temp dir.

Assumed contract (initial assumptions, not optimal values):
- seller_r3.now() is the seller's clock (epoch seconds, same scale as inventory expires_at); tests patch it
- an open negotiation reserves its unit; the reservation goes stale 120s after the task's last buyer turn and is released
- find_unit is FEFO over units with expires_at > now (None = never expires, sorts last); expires_at <= now is out even grade A
- every buyer turn re-reads inventory; a continuing task whose unit is gone, expired or released rejects without signing
- a completed negotiation holds its unit for at most 30s (the kit's inventory snapshot may lag ~10s), then releases it
- journal events: reservation_stale, cancel, unit_unavailable(reason gone|expired|stale), hold_expired;
  each row carries the owning task id and unit id (source), never raw buyer text or signatures
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

SECRET = 'RAW-BUYER-PHRASE-lifecycle'


def unit(uid, expires_at=None, grade='A', supplied_at=1):
    return dict(unit=uid, sku=f'S-{uid}', name='Lifecycle kimchi', category='food>kimchi', grade=grade, kind='goods',
                cost=50, list_price=100, expires_at=expires_at, supplied_at=supplied_at)


class SellerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.t = 1000
        self.signed = []
        fake_sign = lambda rec: self.signed.append(dict(rec)) or dict(rec, sig_seller='offline-signature-placeholder')
        self.patches = [patch.object(seller_r3, 'MARKET_DIR', self.tmp),
                        patch.object(seller_r3, 'ask_json', return_value=(None, '')),   # deterministic fallback
                        patch.object(seller_r3, 'sign', side_effect=fake_sign),
                        patch.object(seller_r3, 'now', create=True, side_effect=lambda: self.t)]
        for p in self.patches:
            p.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=seller_r3.build(9999)))  # one shop, one state

    async def asyncTearDown(self):
        await self.client.aclose()
        for p in reversed(self.patches):
            p.stop()
        self._tmp.cleanup()

    def stock(self, *units):
        Path(self.tmp, 'inventory.json').write_text(json.dumps({'round': 3, 'units': list(units)}), encoding='utf-8')
        return hashlib.sha256(Path(self.tmp, 'inventory.json').read_bytes()).hexdigest()

    async def rpc(self, method, params):
        r = await self.client.post('http://fixture/', headers={'A2A-Version': '1.0'},
                                   json={'jsonrpc': '2.0', 'id': '1', 'method': method, 'params': params})
        body = r.json()
        self.assertNotIn('error', body, body)
        return body['result']

    async def send(self, text, tid=None):
        msg = {'role': 'ROLE_USER', 'messageId': f'm{self.t}-{text}', 'parts': [{'text': text}]}
        if tid:
            msg['taskId'] = tid
        return (await self.rpc('SendMessage', {'message': msg}))['task']

    def state(self, task):
        return task['status']['state']

    def rows(self):
        raw = Path(self.tmp, 'decisions.jsonl').read_text(encoding='utf-8')
        self.assertNotIn(SECRET, raw)
        self.assertNotIn('offline-signature', raw)
        return [json.loads(line) for line in raw.splitlines()]

    def events(self, event, task):
        return [r for r in self.rows() if r['event'] == event and r['task'] == task]

    async def test_stale_reservation_is_released_and_cancel_releases_immediately(self):
        self.stock(unit('u1'))
        a = await self.send(f'{SECRET} I want kimchi')
        self.assertEqual(self.state(a), 'TASK_STATE_INPUT_REQUIRED')

        self.t = 1060                                   # A idle 60s: still reserved
        b = await self.send('I want kimchi')
        self.assertEqual(self.state(b), 'TASK_STATE_REJECTED')

        self.t = 1121                                   # A idle 121s > 120s TTL: released to C
        c = await self.send('I want kimchi')
        self.assertEqual(self.state(c), 'TASK_STATE_INPUT_REQUIRED')
        stale = self.events('reservation_stale', a['id'])
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]['unit'], 'u1')

        self.t = 1122                                   # stale A comes back with a winning offer: no receipt
        a2 = await self.send('100', a['id'])
        self.assertEqual(self.state(a2), 'TASK_STATE_REJECTED')
        gone = self.events('unit_unavailable', a['id'])
        self.assertEqual([(r['unit'], r['reason']) for r in gone], [('u1', 'stale')])

        self.t = 1130                                   # C cancels: unit free at once, not after TTL
        cancelled = await self.rpc('CancelTask', {'id': c['id']})
        self.assertEqual(cancelled['status']['state'], 'TASK_STATE_CANCELED')
        self.assertEqual([r['unit'] for r in self.events('cancel', c['id'])], ['u1'])
        self.t = 1131
        d = await self.send('I want kimchi')
        self.assertEqual(self.state(d), 'TASK_STATE_INPUT_REQUIRED')
        self.assertEqual(self.signed, [])

    async def test_fefo_picks_earliest_future_expiry_and_skips_expired_grade_a(self):
        self.stock(unit('at-now', 1000, 'A'), unit('past', 900, 'A'), unit('later', 1500, 'B'),
                   unit('soonest', 1200, 'C'), unit('never', None, 'A'))
        t = await self.send('I want kimchi')
        self.assertEqual(self.state(t), 'TASK_STATE_INPUT_REQUIRED')
        self.assertEqual(self.events('first_quote', t['id'])[0]['unit'], 'soonest')

        self.stock(unit('at-now', 1000, 'A'), unit('past', 900, 'A'))
        t = await self.send('I want kimchi')
        self.assertEqual(self.state(t), 'TASK_STATE_REJECTED')
        self.assertEqual(len(self.events('no_stock', t['id'])), 1)

    async def test_continuing_task_with_gone_or_expired_unit_rejects_without_signing(self):
        self.stock(unit('g1'))
        a = await self.send('I want kimchi')
        fresh = self.stock()                            # kit sold/removed g1 elsewhere
        self.t = 1010
        a2 = await self.send('100', a['id'])            # 100 would be accepted by fallback
        self.assertEqual(self.state(a2), 'TASK_STATE_REJECTED')
        row = self.events('unit_unavailable', a['id'])
        self.assertEqual([(r['unit'], r['reason'], r['snapshot']) for r in row], [('g1', 'gone', fresh)])

        self.t = 1000
        self.stock(unit('e1', 1100))
        b = await self.send('I want kimchi')
        self.assertEqual(self.state(b), 'TASK_STATE_INPUT_REQUIRED')
        self.t = 1100                                   # expires_at <= now
        b2 = await self.send('100', b['id'])
        self.assertEqual(self.state(b2), 'TASK_STATE_REJECTED')
        self.assertEqual([(r['unit'], r['reason']) for r in self.events('unit_unavailable', b['id'])], [('e1', 'expired')])
        self.assertEqual(self.signed, [])

    async def test_completed_unit_is_held_briefly_never_sold_twice_never_forever(self):
        self.stock(unit('h1'))                          # stays listed: the kit snapshot lags
        a = await self.send('I want kimchi')
        self.t = 1001
        done = await self.send('100', a['id'])
        self.assertEqual(self.state(done), 'TASK_STATE_COMPLETED')
        self.assertEqual([r['unit'] for r in self.signed], ['h1'])

        self.t = 1011                                   # inside the ~10s stale snapshot window
        b = await self.send('I want kimchi')
        self.assertEqual(self.state(b), 'TASK_STATE_REJECTED')
        self.t = 1030
        b = await self.send('I want kimchi')
        self.assertEqual(self.state(b), 'TASK_STATE_REJECTED')

        self.t = 1032                                   # 31s after completion > 30s hold: released
        c = await self.send('I want kimchi')
        self.assertEqual(self.state(c), 'TASK_STATE_INPUT_REQUIRED')
        self.assertEqual([r['unit'] for r in self.events('hold_expired', a['id'])], ['h1'])
        self.assertEqual(len(self.signed), 1)


if __name__ == '__main__':
    unittest.main()
