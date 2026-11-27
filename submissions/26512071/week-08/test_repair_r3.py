"""Round 3 repair regressions (RED first). Offline: no market, no payment, no model request, no local PEM.

Authentic data: logs/claude-repair-20261104/authentic-protocol.json and registry.json, replayed unmodified.
Generated Ed25519 keys are ephemeral and in-memory (or a temp dir for the seller, which reads ./shop_key.pem).
The kit (scripts/market_kit.py, read-only) is imported only for its canon()/verify_seller().
"""
import base64
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

W = Path(__file__).resolve().parent
sys.path.insert(0, str(W))
FIX = W/'logs'/'claude-repair-20261104'
KIT_PATH = W.parents[2]/'scripts'/'market_kit.py'


def load_kit():
    spec = importlib.util.spec_from_file_location('market_kit_readonly', KIT_PATH)
    kit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kit)
    return kit


KIT = load_kit()


def kit_verdict(rec, pub, order):
    """What the actual kit does with a market_buyer receipt before paying (market_kit.py:736-748)."""
    rec = dict(rec)
    if isinstance(rec.get('price'), float) and rec['price'].is_integer():
        rec['price'] = int(rec['price'])                       # :736-737, before any signature check
    if not (rec.get('sig_seller') and KIT.verify_seller(pub, rec)):
        return False                                          # :744
    acceptable = order.get('acceptable') or []
    if rec.get('item') != order['item'] and rec.get('item') not in acceptable:
        return False                                          # :746 category OR acceptable SKU
    return isinstance(rec.get('price'), int) and rec['price'] <= order['budget']   # :747 (ACP :407 also wants int)


def pub_b64(key):
    return base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def captured():
    return json.loads((FIX/'authentic-protocol.json').read_text(encoding='utf-8'))


def completed_receipts():
    out = []
    for case in captured():
        for turn in case['turns']:
            task = turn['response']['result']['task']
            for art in task.get('artifacts', []):
                for part in art.get('parts', []):
                    if isinstance(part.get('data'), dict) and part['data'].get('sig_seller'):
                        out.append((case, part['data']))
    return out


class AuthenticSignatureDiagnosis(unittest.TestCase):
    def test_authentic_receipt_signed_over_exactly_one_price_encoding(self):
        """Diagnosis, not a fix: which canonical price (int vs float) the genuine shop key actually signed."""
        receipts = completed_receipts()
        self.assertEqual(len(receipts), 2)
        for case, rec in receipts:
            with self.subTest(sku=case['sku']):
                self.assertIs(type(rec['price']), float)       # wire value as captured
                body = {k: rec[k] for k in KIT.RECEIPT_KEYS}
                as_float = KIT.verify_seller(case['pubkey'], dict(body, sig_seller=rec['sig_seller']))
                as_int = KIT.verify_seller(case['pubkey'], dict(body, price=int(rec['price']), sig_seller=rec['sig_seller']))
                print(f"\n[diagnosis] {case['sku']} price={rec['price']!r}: verifies_as_float={as_float} verifies_as_int={as_int}")
                self.assertEqual(as_float + as_int, 1, 'genuine signature should match exactly one encoding')

    def test_fixture_pubkey_is_the_registry_key(self):
        registry = json.loads((FIX/'registry.json').read_text(encoding='utf-8'))['shops']
        keys = {s['url'].rstrip('/'): s.get('pubkey') for s in registry}
        for case in captured():
            if case['shop'].rstrip('/') not in keys:
                self.skipTest(f"{case['shop']} absent from captured registry; kit would log 'not a registered shop'")
            self.assertEqual(keys[case['shop'].rstrip('/')], case['pubkey'])


class AuthenticReplay(unittest.IsolatedAsyncioTestCase):
    async def test_replay_sends_exact_protocol_and_agrees_with_kit(self):
        import buyer_r3
        for case in captured():
            with self.subTest(sku=case['sku']):
                turns = list(case['turns'])
                sent = []

                def handler(request):
                    body = json.loads(request.content)
                    sent.append(body)
                    if not turns:
                        return httpx.Response(200, json={'jsonrpc': '2.0', 'id': body['id'], 'error': {'code': -1, 'message': 'unexpected'}})
                    turn = turns.pop(0)
                    return httpx.Response(turn['status'], json=turn['response'])

                order = {'item': 'captured-category-unrecorded', 'acceptable': [case['sku']], 'budget': case['budget']}
                shop = {'name': 'ax-week07', 'url': case['shop'], 'offers': [{'sku': case['sku'], 'qty': 1}]}
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    # Historical baseline transport/canonicalization check: the capture predates bounded-v1's 90% counter,
                    # so replay with the legacy opening. Fixture bytes are replayed unmodified.
                    result = await buyer_r3.negotiate(client, shop, case['sku'], case['budget'], case['pubkey'], order,
                                                      counter_once=False)
                # Exact protocol: same methods, texts and taskId use as the live capture; no CancelTask after a terminal state.
                self.assertEqual(len(sent), len(case['turns']))
                for got, turn in zip(sent, case['turns']):
                    want = turn['request']
                    self.assertEqual(got['method'], want['method'])
                    self.assertEqual(got['params']['message']['parts'], want['params']['message']['parts'])
                    self.assertEqual(got['params']['message'].get('taskId'), want['params']['message'].get('taskId'))
                    self.assertEqual(got['params']['message']['role'], 'ROLE_USER')
                final = case['turns'][-1]['response']['result']['task']
                if final['status']['state'] != 'TASK_STATE_COMPLETED':
                    self.assertIsNone(result['receipt'])
                    continue
                rec = final['artifacts'][0]['parts'][0]['data']
                # Buyer must accept exactly what the kit would sign/pay for; never more, never less.
                self.assertEqual(result['receipt'] is not None, kit_verdict(rec, case['pubkey'], order))


class ReceiptContract(unittest.IsolatedAsyncioTestCase):
    """Synthetic shop with an ephemeral key, speaking the captured message shapes."""

    async def deal(self, item, signed_price, wire_price, order, budget=150):
        import buyer_r3
        key = Ed25519PrivateKey.generate()
        pub = pub_b64(key)
        tid = 'task-fixture-1'
        state = {'n': 0}

        def handler(request):
            body = json.loads(request.content)
            state['n'] += 1
            if body['method'] != 'SendMessage':
                return httpx.Response(200, json={'jsonrpc': '2.0', 'id': body['id'], 'result': {}})
            if state['n'] == 1:
                task = {'id': tid, 'status': {'state': 'TASK_STATE_INPUT_REQUIRED', 'message': {'role': 'ROLE_AGENT',
                        'parts': [{'text': 'Fixture: asking 100. Send your offer as a number.\nReply with a number, or ok to take the ask: 100'}]}}}
            else:
                body_ = {'shop': 'Fixture Shop', 'item': item, 'price': signed_price, 'task': tid}
                rec = dict(body_, price=wire_price, sig_seller=base64.b64encode(key.sign(KIT.canon(body_))).decode())
                task = {'id': tid, 'status': {'state': 'TASK_STATE_COMPLETED'},
                        'artifacts': [{'name': 'receipt', 'parts': [{'data': rec, 'mediaType': 'application/json'}]}]}
            return httpx.Response(200, json={'jsonrpc': '2.0', 'id': body['id'], 'result': {'task': task}})

        shop = {'name': 'fixture', 'url': 'http://fixture', 'offers': [{'sku': 'GSKU1', 'qty': 1}]}
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            # Historical signature/canonicalization fixture: the shop always signs 100, so use the legacy opening
            # (offer the ask). Under bounded-v1 the buyer offers 90 and a 100 receipt is correctly refused.
            result = await buyer_r3.negotiate(client, shop, 'GSKU1', budget, pub, order, counter_once=False)
        return result, pub

    ORDER = {'item': '침구', 'acceptable': ['GSKU1'], 'budget': 150, 'kind': 'goods'}

    async def test_int_signed_receipt_on_float_wire_is_accepted_like_kit(self):
        # A2A data parts carry 100 as 100.0; the kit restores int before verifying (market_kit.py:736).
        result, _ = await self.deal('GSKU1', 100, 100.0, self.ORDER)
        self.assertIsNotNone(result['receipt'])
        self.assertIs(type(result['receipt']['price']), int)

    async def test_category_receipt_is_accepted_like_kit(self):
        result, _ = await self.deal('침구', 100, 100, self.ORDER)
        self.assertIsNotNone(result['receipt'])
        self.assertEqual(result['receipt']['item'], '침구')

    async def test_float_signed_receipt_is_refused_because_kit_cannot_verify_it(self):
        result, pub = await self.deal('GSKU1', 100.0, 100.0, self.ORDER)
        self.assertIsNone(result['receipt'])

    async def test_unrelated_item_non_integer_and_over_budget_still_refused(self):
        for item, signed, wire, budget in [('GOTHER', 100, 100.0, 150), ('GSKU1', 99.5, 99.5, 150)]:
            with self.subTest(item=item, price=signed):
                result, _ = await self.deal(item, signed, wire, self.ORDER, budget)
                self.assertIsNone(result['receipt'])
        # Budget below the ask: the buyer offers at most its budget, so a receipt above it must be refused.
        result, _ = await self.deal('GSKU1', 100, 100.0, dict(self.ORDER, budget=90), 90)
        self.assertIsNone(result['receipt'])


class OwnSellerReceipt(unittest.IsolatedAsyncioTestCase):
    async def test_seller_r3_receipt_verifies_on_the_kit_buyer_path(self):
        """Our shop's signed receipt must survive market_kit.py:736 normalization + verify_seller (both kit sides)."""
        import seller_r3 as seller
        key = Ed25519PrivateKey.generate()
        pub = pub_b64(key)
        old = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                Path(tmp, 'shop_key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                                        serialization.NoEncryption()))
                os.chdir(tmp)
                unit = dict(unit='fixture-unit', sku='GLOCAL', name='Fixture goods', category='a>b>침구', kind='goods', grade='A',
                            cost=50, list_price=200, expires_at=None, supplied_at=1)
                Path(tmp, 'inventory.json').write_text(json.dumps({'round': 3, 'units': [unit]}), encoding='utf-8')
                moves = [({'action': 'counter', 'price': 120}, 'fixture'), ({'action': 'accept'}, 'fixture')]
                with patch.object(seller, 'MARKET_DIR', Path(tmp)), patch.object(seller, 'ask_json', side_effect=moves):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=seller.build(9999))) as client:
                        async def send(text, tid=None):
                            msg = {'role': 'ROLE_USER', 'messageId': f'm-{text[:8]}', 'parts': [{'text': text}]}
                            if tid:
                                msg['taskId'] = tid
                            r = await client.post('http://test/', headers={'A2A-Version': '1.0'},
                                                  json={'jsonrpc': '2.0', 'id': '1', 'method': 'SendMessage', 'params': {'message': msg}})
                            res = r.json()['result']
                            return res.get('task', res)
                        first = await send('I want to buy GLOCAL. Please quote a price in credits.')
                        self.assertEqual(first['status']['state'], 'TASK_STATE_INPUT_REQUIRED')
                        done = await send('120', first['id'])
            finally:
                os.chdir(old)
        self.assertEqual(done['status']['state'], 'TASK_STATE_COMPLETED')
        rec = done['artifacts'][0]['parts'][0]['data']
        order = {'item': '침구', 'acceptable': ['GLOCAL'], 'budget': 150}
        self.assertTrue(kit_verdict(rec, pub, order), f'kit would refuse our receipt: {rec}')


class MerchantDiversity(unittest.TestCase):
    def test_first_three_candidates_span_distinct_shops(self):
        import buyer_r3
        order = {'item': '침구', 'acceptable': ['G1', 'G2', 'G3']}
        offers = lambda *skus: [{'sku': s, 'qty': 1} for s in skus]
        shops = [{'name': 'ax-week07', 'url': 'https://a', 'offers': offers('G1', 'G2', 'G3')},
                 {'name': 'bright-living', 'url': 'https://b', 'offers': offers('G1')},
                 {'name': 'corner-store', 'url': 'https://c', 'offers': offers('G2')},
                 {'name': 'fair-furniture', 'url': 'https://f', 'offers': offers('G3')}]
        first = buyer_r3.candidates(shops, order)[:3]
        self.assertEqual(len(first), 3)
        self.assertEqual({s['url'] for s, _ in first}, {'https://a', 'https://b', 'https://c'})
        self.assertTrue(all(sku in order['acceptable'] for _, sku in first))

    def test_single_shop_keeps_its_skus(self):
        import buyer_r3
        order = {'item': '침구', 'acceptable': ['G1', 'G2', 'G3']}
        shops = [{'name': 'only', 'url': 'https://a', 'offers': [{'sku': s, 'qty': 1} for s in ('G1', 'G2', 'G3')] + [{'sku': 'G9', 'qty': 0}]}]
        self.assertEqual([sku for _, sku in buyer_r3.candidates(shops, order)], ['G1', 'G2', 'G3'])

    def test_authentic_registry_stock_first_three_not_single_merchant(self):
        """Offers built exactly like market_kit.py:698-702 from the captured registry and the captured SKUs."""
        import buyer_r3
        skus = [c['sku'] for c in captured()]
        registry = json.loads((FIX/'registry.json').read_text(encoding='utf-8'))['shops']
        shops = []
        for s in registry:
            shop = {k: s[k] for k in ('name', 'url') if k in s}
            shop['offers'] = [{'sku': k, 'qty': n} for k, n in (s.get('stock') or {}).items() if k in skus]
            shops.append(shop)
        order = {'item': 'captured-category-unrecorded', 'acceptable': skus}
        stocked = {s['url'] for s in shops if s['offers'] and s['name'] != 'fair-furniture'}
        first = buyer_r3.candidates(shops, order)[:3]
        self.assertEqual(len({s['url'] for s, _ in first}), min(3, len(stocked), len(first)))


if __name__ == '__main__':
    unittest.main()
