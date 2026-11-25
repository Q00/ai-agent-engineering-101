"""Same-unit first quotes reuse a published price, never offers or receipt state."""
import json
import unittest
from unittest.mock import patch
from starlette.testclient import TestClient
from opening_quote_cache import OpeningQuoteCache
from inventory import StockError
from seller_agent import DEFAULT, build
from test_adaptive_seller import send
import test_closing as fixtures


class OpeningQuoteCacheTests(unittest.TestCase):
    setUp = fixtures.ClosingTests.setUp
    seller = fixtures.ClosingTests.seller

    def cancel(self, client, task):
        response = client.post('/', headers={'A2A-Version':'1.0'}, json={
            'jsonrpc':'2.0', 'id':'cancel', 'method':'CancelTask', 'params':{'id':task['id']}})
        self.assertNotIn('error', response.json())

    def test_repeat_opening_skips_model_but_keeps_fresh_task_and_receipt(self):
        seller, key = self.seller()
        ticks = [100.0]
        seller.opening_quotes = OpeningQuoteCache(clock=lambda:ticks[0])
        with patch('seller_r3.inventory_decision', return_value=fixtures.counter(90000)) as model, \
                TestClient(build(DEFAULT, seller)) as client:
            first = send(client, 'G00001')
            self.cancel(client, first)
            ticks[0] = 150
            second = send(client, 'G00001')
            self.assertNotEqual(first['id'], second['id'])
            self.assertEqual(model.call_count, 1)
            self.assertEqual(second['status']['message']['metadata']['ask'], 90000)
            self.cancel(client, second)
            ticks[0] = 160  # A cache hit must not extend the original deadline.
            third = send(client, 'G00001')
            self.assertEqual(model.call_count, 2)
            done = send(client, '90000', third['id'])
            receipt = fixtures.ClosingTests.receipt(self, done, key, 90000)
            self.assertEqual(receipt['task'], third['id'])
        events=[json.loads(x) for x in seller.events.read_text().splitlines()]
        self.assertIn('recent_unit_quote', [e['decision_source'] for e in events])
        cancelled=[e for e in events if e.get('reason_code')=='buyer_cancelled']
        self.assertEqual(cancelled[0]['list_price'], 100000)
        self.assertEqual(cancelled[0]['quoted_ask'], 90000)

    def test_inventory_terms_and_bounds_changes_force_new_decision(self):
        product={'unit':'u1','sku':'G1','kind':'goods','grade':'A','cost':50,'list_price':100,'cert':{'x':1}}
        deal={'round':3,'terms':{'warranty_hours':6},'negotiation':{'price_floor':55}}
        cache=OpeningQuoteCache(clock=lambda:1)
        key=cache.key(product,deal);cache.remember(key,70)
        for field,value in [('unit','u2'),('sku','G2'),('grade','B'),('cost',60),('list_price',80),('cert',{'x':2}),('expires_at',100)]:
            self.assertIsNone(cache.get(cache.key({**product,field:value},deal)))
        for changed in ({**deal,'round':4},{**deal,'terms':{'warranty_hours':0}},
                        {**deal,'negotiation':{'price_floor':75}}):
            self.assertIsNone(cache.get(cache.key(product,changed)))
        self.assertEqual(cache.get(key)['price'],70)
        for i in range(100):cache.remember(str(i),70)
        self.assertLessEqual(len(cache.rows),64)

    def test_cached_quote_does_not_avoid_inventory_unavailability(self):
        seller,_=self.seller()
        with patch('seller_r3.inventory_decision', return_value=fixtures.counter(90000)) as model, \
                TestClient(build(DEFAULT,seller)) as client:
            first=send(client,'G00001');self.cancel(client,first)
            with patch.object(seller.inventory,'reserve', side_effect=StockError('No stock')):
                rejected=send(client,'G00001')
            self.assertEqual(rejected['status']['state'],'TASK_STATE_REJECTED')
            self.assertEqual(model.call_count,1)
