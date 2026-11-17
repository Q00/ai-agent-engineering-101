"""seller_r3 x sol-sku-policy-v1: Sol values actually drive start/target/min of NEW tasks; in-flight snapshots,
non-increasing quotes, cost floor, list bound and receipt guards preserved. Offline: fake Sol/Terra, temp state."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import openai_harness
import seller_r3
import sol_policy

HERE = Path(__file__).resolve().parent


def unit(uid='u1', sku='S1', cost=50, lst=100):
    return dict(unit=uid, sku=sku, name='Sol lamp', category='home>lamp', grade='A', kind='goods', cost=cost,
                list_price=lst, expires_at=None, supplied_at=1)


def sol_reply(start, target, mn, sku='S1'):
    return lambda prompt: {'text':json.dumps({'skus':{sku:{'start_ask':start, 'target_settle':target, 'min_accept':mn,
                                                         'rationale_refs':['own_cost']}}}),
                           'requested':'gpt-6.1-sol', 'returned':'gpt-6.1-sol', 'http_status':200,
                           'usage':{'input':500, 'output':60, 'reasoning':0}, 'latency_s':0.9, 'finish_reason':'stop',
                           'cost_usd':0.0016, 'error':None, 'reasoning_effort':'low'}


class SellerSolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.t = 1_800_000_000.0
        self.moves = []
        self.signed = []
        fake_sign = lambda rec: self.signed.append(dict(rec)) or dict(rec, sig_seller='offline')
        self.active = True

        def ask(prompt):
            self.prompts.append(prompt)
            openai_harness.LAST_CALL.clear()
            openai_harness.LAST_CALL.update(requested='gpt-6.1-terra', returned='gpt-6.1-terra', input=400, output=12, latency_s=0.7)
            m = self.moves.pop(0) if self.moves else None
            return m, json.dumps(m)
        self.prompts = []
        self.patches = [patch.object(seller_r3, 'MARKET_DIR', self.tmp),
                        patch.object(seller_r3, 'ask_json', side_effect=ask),
                        patch.object(seller_r3, 'sign', side_effect=fake_sign),
                        patch.object(seller_r3, 'now', side_effect=lambda: self.t),
                        patch.object(sol_policy, 'active', side_effect=lambda *a, **k: self.active),
                        patch.object(sol_policy, 'maybe_refresh_async', return_value=False)]
        for p in self.patches:
            p.start()
        (self.tmp/'inventory.json').write_text(json.dumps({'round':3, 'units':[unit('u1'), unit('u2')]}), encoding='utf-8')
        (self.tmp/sol_policy.CONFIG).write_text(json.dumps({'enabled':True}), encoding='utf-8')
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=seller_r3.build(9999)))

    async def asyncTearDown(self):
        await self.client.aclose()
        for p in reversed(self.patches):
            p.stop()
        self._tmp.cleanup()

    def policy(self, start, target, mn):
        got = sol_policy.refresh(self.tmp, now=self.t, call=sol_reply(start, target, mn), force=True)
        self.assertEqual(got['status'], 'updated', got)

    async def send(self, text, tid=None):
        msg = {'role':'ROLE_USER', 'messageId':f'm{self.t}-{text}-{len(self.prompts)}', 'parts':[{'text':text}]}
        if tid:
            msg['taskId'] = tid
        r = await self.client.post('http://fixture/', headers={'A2A-Version':'1.0'},
                                   json={'jsonrpc':'2.0', 'id':'1', 'method':'SendMessage', 'params':{'message':msg}})
        return r.json()['result']['task']

    def rows(self):
        return [json.loads(x) for x in (self.tmp/'decisions.jsonl').read_text(encoding='utf-8').splitlines()]

    def quotes(self, tid):
        return [r['quote'] for r in self.rows() if r.get('task') == tid and r['event'] in ('first_quote', 'counter')]

    def test_modules_are_the_candidate(self):
        for m in (seller_r3, sol_policy, openai_harness):
            self.assertEqual(Path(m.__file__).resolve().parent, HERE, m)

    async def test_altered_legal_candidate_changes_the_opening_quote(self):
        self.policy(83, 78, 70)                       # baseline would open at 90 (90% of list)
        self.moves = [None]                           # Terra unavailable -> deterministic fallback opening
        a = await self.send('I want S1')
        self.assertEqual(self.quotes(a['id']), [83])
        self.policy(95, 90, 85)                       # a different legal policy -> a different quote for a NEW task
        self.moves = [None]
        b = await self.send('I want S1')
        self.assertEqual(self.quotes(b['id']), [95])

    @patch.object(seller_r3, 'FASTPATH', False)   # model-specific: harness path

    async def test_policy_use_is_recorded_separately_from_price_improvement(self):
        self.policy(90, 85, 60)                       # Sol start == baseline 90: no price change, still a real use
        self.moves = [{'action':'counter', 'price':90}]
        a = await self.send('I want S1')
        row = [r for r in self.rows() if r['event'] == 'first_quote'][0]
        sol = row['price_policy']['sol']
        self.assertTrue(sol['used'])
        self.assertEqual((sol['returned_model'], sol['start_ask'], sol['target_settle'], sol['min_accept']), ('gpt-6.1-sol', 90, 85, 60))
        self.assertIn(row['price_policy']['baseline_anchor'], (None, 90))   # baseline was 90% of list
        self.assertEqual(sol['baseline_start'], 90)
        for k in ('policy_id', 'facts_sha256', 'root_sha256'):
            self.assertTrue(sol[k])
        self.assertEqual(row['model_call']['returned'], 'gpt-6.1-terra')
        self.assertIn('Target settlement 85.', self.prompts[0])
        self.assertIn('lowest acceptable price is 60', self.prompts[0])

    async def test_min_accept_floors_concessions_and_acceptance(self):
        self.policy(90, 80, 75)
        self.moves = [None]
        a = await self.send('I want S1')
        for offer in ('50', '50', '50', '50'):
            self.moves = [None]
            a = await self.send(offer, a['id'])
        q = self.quotes(a['id'])
        self.assertEqual(q, [90, 85, 80, 75, 75])
        self.assertEqual(q, sorted(q, reverse=True))  # never rises
        self.moves = [{'action':'accept'}]
        a = await self.send('60', a['id'])           # model accepts below Sol min -> guard counters instead
        self.assertEqual(self.signed, [])
        self.moves = [{'action':'counter', 'price':10}]
        a = await self.send('60', a['id'])           # model counter below min -> clamped to min
        self.assertEqual(self.quotes(a['id'])[-1], 75)
        self.moves = [{'action':'accept'}]
        a = await self.send('75', a['id'])
        self.assertEqual([s['price'] for s in self.signed], [75])

    async def test_mixed_cost_sku_signs_an_above_cost_offer_below_the_dearest_unit_cost(self):
        # live G2b7e0fe3 /100: quoted unit cost 37, a sibling unit cost 111; buyers offered ~92 (median best offer 9169)
        units = [dict(unit('u1', cost=37, lst=148), supplied_at=1), dict(unit('u2', cost=111, lst=148), supplied_at=2)]
        (self.tmp/'inventory.json').write_text(json.dumps({'round':3, 'units':units}), encoding='utf-8')
        self.policy(118, 110, 80)
        self.moves = [None]
        a = await self.send('I want S1')
        for _ in range(5):
            if self.signed:
                break
            self.moves = [None]
            a = await self.send('92', a['id'])
        q = self.quotes(a['id'])
        self.assertEqual(q, sorted(q, reverse=True))  # never rises
        self.assertEqual([(s['price'], s['unit']) for s in self.signed], [(92, 'u1')])
        self.assertGreater(self.signed[0]['price'], 37)

    @patch.object(seller_r3, 'FASTPATH', False)   # model-specific: harness path

    async def test_in_flight_task_keeps_its_snapshot(self):
        self.policy(90, 85, 80)
        self.moves = [None]
        a = await self.send('I want S1')
        self.policy(99, 95, 95)                       # later policy: new tasks only
        self.moves = [{'action':'counter', 'price':99}]
        a = await self.send('70', a['id'])
        self.assertEqual(self.quotes(a['id']), [90, 90])  # capped by the last ask, not the new start
        self.moves = [None]
        a = await self.send('70', a['id'])
        self.assertEqual(self.quotes(a['id'])[-1], 85)     # old min 80 floor, concession continues

    async def test_inactive_or_stale_keeps_tested_baseline(self):
        self.policy(83, 78, 70)
        self.active = False
        self.moves = [None]
        a = await self.send('I want S1')
        self.assertEqual(self.quotes(a['id']), [90])
        self.assertEqual(self.rows()[-1]['price_policy']['sol'], {'used':False, 'reason':'inactive'})
        self.active = True
        self.t += 7201
        self.moves = [None]
        b = await self.send('I want S1')
        self.assertEqual(self.quotes(b['id']), [90])
        self.assertEqual(self.rows()[-1]['price_policy']['sol']['reason'], 'sol policy outside TTL')

    async def test_cost_floor_and_list_bound_hold_for_extreme_policy(self):
        self.policy(10_000, 10_000, 10_000)
        self.moves = [None]
        a = await self.send('I want S1')
        self.assertEqual(self.quotes(a['id']), [100])  # list bound
        self.policy(1, 1, 1)
        self.moves = [None]
        b = await self.send('I want S1')
        self.assertEqual(self.quotes(b['id']), [50])   # cost floor


if __name__ == '__main__':
    unittest.main()
