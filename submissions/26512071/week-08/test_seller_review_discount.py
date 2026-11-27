"""Review-discount pilot through the REAL seller path: staged seller_r3.build() over A2A JSON-RPC (httpx ASGI), deterministic
fallback (ask_json patched to no decision), fake signer, temp MARKET_DIR. No network, no model calls, no real key.
Integration route: PYTHONPATH=<stage>;<week-08> so `import seller_r3` loads the STAGED seller (asserted below)."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import review_discount as rd
import seller_r3
from test_review_discount import OWN, OWN_OWNER, POST, REAL_SETTLE, OWNERS, T0

STAGE = Path(__file__).resolve().parent
UNIT = dict(unit='u-rd-1', sku='Grd1', name='Review desk', category='furniture>desk', grade='A', kind='goods',
            cost=9000, list_price=20000, expires_at=None, supplied_at=1)


class SellerReviewDiscountTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.t = T0 + 100
        self.signed = []
        fake_sign = lambda rec: self.signed.append(dict(rec)) or dict(rec, sig_seller='offline-signature-placeholder')
        self.patches = [patch.object(seller_r3, 'MARKET_DIR', self.tmp),
                        patch.object(seller_r3, 'ask_json', return_value=(None, '')),
                        patch.object(seller_r3, 'sign', side_effect=fake_sign),
                        patch.object(seller_r3, 'price_policy', return_value=(None, {'anchor': None, 'label': 'fixture'})),
                        patch.object(seller_r3, 'now', side_effect=lambda: self.t)]
        for p in self.patches:
            p.start()
        Path(self.tmp, 'inventory.json').write_text(json.dumps({'round': 3, 'units': [UNIT]}), encoding='utf-8')
        cfg = dict(pilot=rd.PILOT, activated_at=T0, expires_at=T0 + 86400, max_pct=5, budget=10000, own_owner=OWN_OWNER)
        Path(self.tmp, rd.CONFIG).write_text(json.dumps(cfg), encoding='utf-8')
        got, _ = rd.verify_post(POST, {REAL_SETTLE['seq']: REAL_SETTLE}, OWN, OWNERS, OWN_OWNER)
        Path(self.tmp, rd.EVIDENCE).write_text(json.dumps({'version': rd.EVIDENCE_VERSION, 'eligible': {'40001': got}}),
                                               encoding='utf-8')
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=seller_r3.build(9999)))

    async def asyncTearDown(self):
        await self.client.aclose()
        for p in reversed(self.patches):
            p.stop()
        self._tmp.cleanup()

    async def send(self, text, tid=None):
        msg = {'role': 'ROLE_USER', 'messageId': f'm{self.t}-{text}', 'parts': [{'text': text}]}
        if tid:
            msg['taskId'] = tid
        r = await self.client.post('http://fixture/', headers={'A2A-Version': '1.0'},
                                   json={'jsonrpc': '2.0', 'id': '1', 'method': 'SendMessage', 'params': {'message': msg}})
        body = r.json()
        self.assertNotIn('error', body, body)
        return body['result']['task']

    async def cancel(self, tid):
        r = await self.client.post('http://fixture/', headers={'A2A-Version': '1.0'},
                                   json={'jsonrpc': '2.0', 'id': '2', 'method': 'CancelTask', 'params': {'id': tid}})
        self.assertEqual(r.json()['result']['status']['state'], 'TASK_STATE_CANCELED')

    def reply(self, task):
        return ' '.join(p.get('text', '') for p in task['status']['message']['parts'])

    def ledger(self):
        return json.loads(Path(self.tmp, rd.LEDGER).read_text(encoding='utf-8'))['reservations']

    def rows(self, event):
        raw = Path(self.tmp, 'decisions.jsonl').read_text(encoding='utf-8')
        return [json.loads(x) for x in raw.splitlines() if json.loads(x)['event'] == event]

    def test_integration_route_imports_the_staged_seller(self):
        self.assertEqual(Path(seller_r3.__file__).resolve().parent, STAGE)
        self.assertEqual(Path(rd.__file__).resolve().parent, STAGE)

    async def test_card_states_pilot_only_while_active(self):
        r = await self.client.get('http://fixture/.well-known/agent-card.json')
        self.assertIn('검증 가능한 실제 거래후기 게시글에 한해 다음 거래 할인', r.json()['description'])
        self.assertIn('REVIEW-COUPON', r.json()['skills'][0]['description'])
        Path(self.tmp, rd.CONFIG).unlink()
        card = (await httpx.AsyncClient(transport=httpx.ASGITransport(app=seller_r3.build(9999))).get(
            'http://fixture/.well-known/agent-card.json')).json()
        self.assertNotIn('REVIEW-COUPON', json.dumps(card, ensure_ascii=False))
        self.assertTrue(card['description'].startswith('A small online shop.'))

    async def test_no_coupon_keeps_normal_quote_and_touches_no_pilot_file(self):
        a = await self.send('I want Review desk Grd1')
        self.assertIn('asking 18000', self.reply(a))                 # 90% of list, the unchanged bounded schedule
        self.assertFalse(Path(self.tmp, rd.LEDGER).exists())
        self.assertNotIn('review_discount', self.rows('first_quote')[0])

    async def test_valid_coupon_discounts_signs_and_reserves_but_does_not_redeem(self):
        a = await self.send('I want Review desk Grd1. REVIEW-COUPON 40001')
        self.assertIn('asking 17100', self.reply(a))                 # 18000 - 5%
        row = self.rows('first_quote')[0]['review_discount']
        self.assertEqual((row['post'], row['settle_seq'], row['normal_quote'], row['discount'], row['discounted_quote'],
                          row['cost']), (40001, 3605905, 18000, 900, 17100, 9000))
        done = await self.send('17100', a['id'])
        self.assertEqual(done['status']['state'], 'TASK_STATE_COMPLETED')
        self.assertEqual(self.signed[0]['price'], 17100)
        self.assertEqual([r['status'] for r in self.ledger()], ['signed'])   # receipt is not settlement
        self.assertEqual(self.rows('negotiation_complete')[0]['review_discount']['status'], 'signed')

    async def test_second_task_same_coupon_pays_regular_price(self):
        Path(self.tmp, 'inventory.json').write_text(json.dumps({'round': 3, 'units': [UNIT, dict(UNIT, unit='u-rd-2')]}),
                                                    encoding='utf-8')
        a = await self.send('I want Review desk Grd1. REVIEW-COUPON 40001')
        b = await self.send('I want Review desk Grd1. REVIEW-COUPON 40001')
        self.assertIn('asking 17100', self.reply(a))
        self.assertIn('asking 18000', self.reply(b))
        self.assertEqual(self.rows('first_quote')[1]['review_discount'], {'post': 40001, 'outcome': 'already_used'})

    async def test_unverified_or_corrupt_ledger_falls_back_to_regular_price(self):
        a = await self.send('I want Review desk Grd1. REVIEW-COUPON 49999')
        self.assertIn('asking 18000', self.reply(a))
        await self.cancel(a['id'])
        Path(self.tmp, rd.LEDGER).write_text('{broken', encoding='utf-8')
        b = await self.send('I want Review desk Grd1. REVIEW-COUPON 40001')
        self.assertIn('asking 18000', self.reply(b))
        self.assertEqual(self.rows('first_quote')[1]['review_discount']['outcome'], 'ledger_corrupt')
        done = await self.send('18000', b['id'])                     # regular sales continue
        self.assertEqual(done['status']['state'], 'TASK_STATE_COMPLETED')

    async def test_cancel_and_stale_release_unsigned_reservation(self):
        a = await self.send('I want Review desk Grd1. REVIEW-COUPON 40001')
        await self.cancel(a['id'])
        self.assertEqual([r['status'] for r in self.ledger()], ['released'])
        b = await self.send('I want Review desk Grd1. REVIEW-COUPON 40001')
        self.assertIn('asking 17100', self.reply(b))
        self.t += seller_r3.RESERVATION_TTL + 1
        c = await self.send('I want Review desk Grd1')               # sweep releases b's stale reservation
        self.assertEqual([r['status'] for r in self.ledger()], ['released', 'released'])
        self.assertIn('asking 18000', self.reply(c))

    async def test_discounted_deal_never_signs_at_or_below_cost(self):
        a = await self.send('I want Review desk Grd1. REVIEW-COUPON 40001')
        for _ in range(40):                                          # buyer insists on cost: never accepted
            a = await self.send('9000', a['id'])
            if a['status']['state'] != 'TASK_STATE_INPUT_REQUIRED':
                break
        self.assertEqual(self.signed, [])
        self.assertTrue(all(r['quote'] >= 9001 for r in self.rows('counter')))


if __name__ == '__main__':
    unittest.main()
